"""One scheduled run: renew the session, then move each card's leftover to Credit."""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os

from . import config
from .api import Client, SessionExpired, TenbisError


def scheduled_today(cfg: dict, today: dt.date) -> tuple[bool, str]:
    if config.DAYS[today.weekday()] not in cfg["days"]:
        return False, f"{today:%A} is not a scheduled day"
    if cfg["mode"] == "monthly" and today != last_scheduled_day(today, cfg["days"]):
        return False, "monthly mode: not the last scheduled day of the month"
    return True, ""


def last_scheduled_day(today: dt.date, days: list[str]) -> dt.date:
    day = dt.date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    while config.DAYS[day.weekday()] not in days:
        day -= dt.timedelta(days=1)
    return day


def log_event(**fields) -> dict:
    event = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **fields}
    os.makedirs(config.state_dir(), exist_ok=True)
    with open(config.log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def run(client: Client, cfg: dict, dry_run=False, force=False, today: dt.date | None = None) -> list[dict]:
    """Returns the events it logged. Raises SessionExpired / TenbisError on failure."""
    today = today or dt.date.today()
    if not force:
        ok, reason = scheduled_today(cfg, today)
        if not ok:
            return [log_event(action="skip", reason=reason)]

    try:
        client.refresh()
    except SessionExpired:
        raise
    except TenbisError as e:  # the budget call below will tell if the session is really gone
        log_event(action="warn", reason=f"session refresh failed: {e}")

    cards = client.convertible_cards()
    if not cards:
        return [log_event(action="skip", reason="no card allows moving budget to 10bis Credit")]

    events = []
    for card in cards:
        if card.available < cfg["min_amount"]:
            events.append(log_event(action="skip", card=card.suffix, available=card.available,
                                    reason="nothing left to move"))
            continue
        amount = round(min(card.available, cfg["max_amount_per_run"]), 2)
        if dry_run:
            events.append(log_event(action="dry_run", card=card.suffix, would_move=amount))
            continue
        client.load_credit(card, amount)
        events.append(log_event(action="moved", card=card.suffix, amount=amount))

    if any(e["action"] == "moved" for e in events):
        left = {c.suffix: c.available for c in client.convertible_cards()}
        for e in events:
            if e["action"] == "moved":
                e["left"] = left.get(e["card"])
    return events
