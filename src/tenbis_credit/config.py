"""Settings and file locations. Everything lives in the user's own profile."""
from __future__ import annotations

import json
import os
import sys

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # index == date.weekday()

DEFAULTS = {
    "email": "",
    # "auto": follow each card's budget type (companies differ): a daily budget
    #   moves every scheduled day, a weekly one on the last scheduled day of the
    #   week, a monthly one on the last scheduled day of the month.
    # "monthly": move every card only on the last scheduled day of the month.
    # ("daily", the 0.1.x default, means "auto": a monthly budget is never
    #   moved daily, which would leave nothing for meals.)
    "mode": "auto",
    "days": ["sun", "mon", "tue", "wed", "thu"],
    "time": "22:00",
    "min_amount": 1.0,
    "max_amount_per_run": 1000.0,
    "notify": True,
    # Override individual URLs from api.ENDPOINTS if 10bis moves one.
    "endpoints": {},
}


def _base(env: str, windows_env: str, posix_default: str) -> str:
    home = os.environ.get("TENBIS_CREDIT_HOME")
    if home:
        return home
    if sys.platform == "win32":
        return os.path.join(os.environ.get(windows_env) or os.path.expanduser("~"), "tenbis-credit")
    return os.path.join(os.environ.get(env) or os.path.expanduser(posix_default), "tenbis-credit")


def config_dir() -> str:
    return _base("XDG_CONFIG_HOME", "APPDATA", "~/.config")


def state_dir() -> str:
    return _base("XDG_STATE_HOME", "LOCALAPPDATA", "~/.local/state")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def cookies_path() -> str:
    return os.path.join(config_dir(), "cookies.txt")


def pending_login_path() -> str:
    return os.path.join(config_dir(), "pending_login.json")


def log_path() -> str:
    return os.path.join(state_dir(), "log.jsonl")


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(config_path()):
        with open(config_path(), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    # Only full URLs count as overrides (older versions stored relative paths).
    cfg["endpoints"] = {k: v for k, v in (cfg["endpoints"] or {}).items()
                        if isinstance(v, str) and v.startswith("https://")}
    if cfg["mode"] == "daily":
        cfg["mode"] = "auto"
    validate(cfg)
    return cfg


def save(cfg: dict):
    validate(cfg)
    write_private(config_path(), json.dumps(cfg, indent=2, ensure_ascii=False))


def validate(cfg: dict):
    if cfg["mode"] not in ("auto", "daily", "monthly"):
        raise ValueError(f"mode must be 'auto' or 'monthly', not {cfg['mode']!r}")
    bad = [d for d in cfg["days"] if d not in DAYS]
    if bad or not cfg["days"]:
        raise ValueError(f"days must be a non-empty list from {DAYS}, got {cfg['days']}")
    hh, _, mm = str(cfg["time"]).partition(":")
    if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
        raise ValueError(f"time must be HH:MM, got {cfg['time']!r}")


def write_private(path: str, text: str):
    """Write a file only the current user can read (it may hold session data)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
