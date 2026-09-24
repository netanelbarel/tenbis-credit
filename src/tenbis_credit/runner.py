"""One scheduled run: renew the session, then move each card's leftover to Credit."""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os

from . import config
from .api import Card, Client, SessionExpired, TenbisError


def scheduled_today(cfg: dict, today: dt.date) -> tuple[bool, str]:
    if config.DAYS[today.weekday()] not in cfg["days"]:
        return False, f"{today:%A} is not a scheduled day"
    return True, ""


def card_due(card: Card, cfg: dict, today: dt.date) -> tuple[bool, str]:
    """Is this card's leftover about to expire today? Depends on how its company budgets."""
    if card.auto_credit_on:
        return False, "10bis automatic credit is on for this card, so 10bis moves it itself"
    period = "monthly" if cfg["mode"] == "monthly" else card.period
    if period == "weekly" and today != last_scheduled_day_of_week(today, cfg["days"]):
        return False, "weekly budget: moved on the last scheduled day of the week"
    if period == "monthly" and today != last_scheduled_day(today, cfg["days"]):
        return False, "monthly budget: moved on the last scheduled day of the month"
    return True, ""


def last_scheduled_day(today: dt.date, days: list[str]) -> dt.date:
    day = dt.date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    while config.DAYS[day.weekday()] not in days:
        day -= dt.timedelta(days=1)
    return day


def last_scheduled_day_of_week(today: dt.date, days: list[str]) -> dt.date:
    """Weeks run Sunday to Saturday, as in Israel."""
    day = today + dt.timedelta(days=(5 - today.weekday()) % 7)  # this week's Saturday
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
    """Returns the events it logged. Raises SessionExpired / TenbisError on failure.

    force skips only the day-of-week check. A weekly or monthly budget still
    waits for its last day, so a forced run can't empty it early.
    """
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
        due, reason = card_due(card, cfg, today)
        if not due:
            events.append(log_event(action="skip", card=card.suffix, available=card.available, reason=reason))
            continue
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
