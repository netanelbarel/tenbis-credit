"""tenbis-credit command line."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys

from . import __version__, config, notify, runner, scheduler
from .api import Client, SessionExpired, TenbisError

EXIT_SESSION_EXPIRED = 2
EXIT_ERROR = 1


def client(cfg: dict) -> Client:
    return Client(config.cookies_path(), cfg["endpoints"])


def ask(prompt: str, default: str = "") -> str:
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    return input(shown).strip() or default


WHEN = {"daily": "every scheduled evening",
        "weekly": "on the last scheduled day of each week",
        "monthly": "on the last scheduled day of each month"}


def print_cards(c: Client, cfg: dict):
    cards = c.convertible_cards()
    if not cards:
        print("  No card on this account allows moving budget to 10bis Credit.")
        print("  (Your employer controls this in 10bis. Check the app under your card.)")
    for card in cards:
        print("  " + card.describe())
        if card.auto_credit_on:
            print("    10bis automatic credit is already on for this card, so this tool leaves it alone.")
            continue
        period = "monthly" if cfg["mode"] == "monthly" else card.period
        print(f"    Will be moved {WHEN[period]}.")
        if card.auto_credit_offered:
            print("    Tip: your company offers 10bis's own automatic credit for this card.")
            print("    Turning it on in the 10bis app is the official way, and needs no computer.")
    return cards


# ---------- login ----------

def do_login(cfg: dict, email: str | None, code: str | None) -> bool:
    """Interactive, or two-step (--email then --code) for scripts. True when logged in."""
    c = client(cfg)
    pending = config.pending_login_path()
    if code:
        if not os.path.exists(pending):
            sys.exit("No login in progress. Run `tenbis-credit login --email you@example.com` first.")
        with open(pending, encoding="utf-8") as f:
            state = json.load(f)
        os.remove(pending)
        c.verify_code(state["email"], state["auth"], code)
        cfg["email"] = state["email"]
    else:
        if not email:
            # Show the saved email as the default, so it's clear which account is used.
            email = ask("Your 10bis email", cfg["email"]) if sys.stdin.isatty() else cfg["email"]
        if not email:
            sys.exit("No email given. Use: tenbis-credit login --email you@example.com")
        auth = c.send_code(email)
        where = "by SMS" if auth.get("sendingMethod") == "Phone" else "by email"
        if not sys.stdin.isatty():
            config.write_private(pending, json.dumps({"email": email, "auth": auth}))
            print(f"10bis sent a code {where}. Finish with: tenbis-credit login --code <code>")
            return False
        code = ask(f"Enter the code 10bis sent you {where}")
        c.verify_code(email, auth, code)
        cfg["email"] = email
    config.save(cfg)
    print(f"✓ Logged in as {cfg['email']}")
    return True


def cmd_login(args, cfg):
    if do_login(cfg, args.email, args.code):
        print_cards(client(cfg), cfg)


# ---------- setup ----------

def cmd_setup(args, cfg):
    print("tenbis-credit setup — moves your unused 10bis budget into 10bis Credit.\n")
    if not do_login(cfg, args.email, None):
        return
    cards = print_cards(client(cfg), cfg)
    if not cards:
        return

    print("\nWhen should it run? Budgets reset at midnight, so pick an evening time.")
    print("Mode 'auto' moves each card when its budget is about to reset (daily, weekly or")
    print("monthly, as your company set it). 'monthly' waits for the month's last work day.")
    cfg["mode"] = ask("Mode: 'auto' or 'monthly'", cfg["mode"])
    cfg["days"] = [d.strip().lower()[:3] for d in
                   ask("Work days", ",".join(cfg["days"])).split(",") if d.strip()]
    cfg["time"] = ask("Time (HH:MM, 24h)", cfg["time"])
    config.save(cfg)
    print(f"✓ {scheduler.install(cfg)}")

    today = dt.date.today()
    due_now = [c for c in cards if runner.card_due(c, cfg, today)[0] and c.available >= cfg["min_amount"]]
    if (due_now and runner.scheduled_today(cfg, today)[0]
            and ask("Move today's leftover now? (y/n)", "y") == "y"):
        _run_and_report(cfg, dry_run=False, force=False)
    print("\nDone. Check anytime with `tenbis-credit status`.")


# ---------- run / status ----------

def _run_and_report(cfg: dict, dry_run: bool, force: bool) -> int:
    try:
        events = runner.run(client(cfg), cfg, dry_run=dry_run, force=force)
    except SessionExpired as e:
        runner.log_event(action="error", error=f"session expired: {e}")
        _notify(cfg, "10bis login expired. Run: tenbis-credit login")
        print("10bis login expired. Run: tenbis-credit login", file=sys.stderr)
        return EXIT_SESSION_EXPIRED
    except TenbisError as e:
        runner.log_event(action="error", error=str(e))
        _notify(cfg, f"Failed: {e}")
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR

    for e in events:
        if e["action"] == "moved":
            msg = f"Moved {e['amount']:g} ₪ from card …{e['card']} to 10bis Credit"
            _notify(cfg, msg)
            print("✓ " + msg)
        elif e["action"] == "dry_run":
            print(f"(dry run) would move {e['would_move']:g} ₪ from card …{e['card']}")
        else:
            print(f"- {e.get('reason', e['action'])}")
    return 0


def _notify(cfg: dict, message: str):
    if cfg.get("notify", True):
        notify.notify(message)


def cmd_run(args, cfg):
    sys.exit(_run_and_report(cfg, dry_run=args.dry_run, force=args.force))


def cmd_status(args, cfg):
    print(f"tenbis-credit {__version__}")
    print(f"  account:  {cfg['email'] or '(not logged in)'}")
    print(f"  schedule: {scheduler.describe(cfg)}, {cfg['mode']} "
          f"({'installed' if scheduler.is_installed() else 'NOT installed: run `tenbis-credit schedule install`'})")
    c = client(cfg)
    expiry = c.session_expiry()
    if expiry:
        print(f"  session:  valid until {expiry:%d/%m/%Y} (renewed on every run)")
    if cfg["email"]:
        try:
            print_cards(c, cfg)
        except SessionExpired:
            print("  10bis login expired. Run: tenbis-credit login")
        except TenbisError as e:
            print(f"  could not read budget: {e}")
    if os.path.exists(config.log_path()):
        with open(config.log_path(), encoding="utf-8") as f:
            last = f.readlines()[-5:]
        print("  recent runs:")
        for line in last:
            e = json.loads(line)
            detail = (f"moved {e['amount']:g} ₪" if e["action"] == "moved"
                      else e.get("reason") or e.get("error") or e["action"])
            print(f"    {e['ts'].replace('T', ' ')}  {detail}")


def cmd_schedule(args, cfg):
    print(scheduler.install(cfg) if args.action == "install" else scheduler.remove())


def cmd_uninstall(args, cfg):
    if not args.yes and ask("Remove the schedule, your saved 10bis login and settings? (y/n)", "n") != "y":
        return
    print(scheduler.remove())
    for d in {config.config_dir(), config.state_dir()}:
        shutil.rmtree(d, ignore_errors=True)
    print("Removed settings and session. Uninstall the package with: pipx uninstall tenbis-credit")


# ---------- entry point ----------

def use_utf8_console():
    """Windows consoles often default to cp1252, which can't print ✓ or ₪ and would crash."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") \
                and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None):
    use_utf8_console()
    p = argparse.ArgumentParser(prog="tenbis-credit",
                                description="Move your unused 10bis budget into 10bis Credit automatically.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="guided login and schedule install (start here)")
    s.add_argument("--email")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("login", help="log in with the one-time code 10bis sends you")
    s.add_argument("--email")
    s.add_argument("--code", help="finish a login started with --email (non-interactive)")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("status", help="login, schedule, available budget and recent runs")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("run", help="move the leftover now (what the schedule runs)")
    s.add_argument("--dry-run", action="store_true", help="show what would move, move nothing")
    s.add_argument("--force", action="store_true", help="run even on a non-scheduled day")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("schedule", help="install or remove the scheduled run")
    s.add_argument("action", choices=["install", "remove"])
    s.set_defaults(fn=cmd_schedule)

    s = sub.add_parser("uninstall", help="remove the schedule, saved login and settings")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_uninstall)

    args = p.parse_args(argv)
    try:
        cfg = config.load()
    except ValueError as e:
        sys.exit(f"Bad setting in {config.config_path()}: {e}")
    try:
        args.fn(args, cfg)
    except SessionExpired:
        sys.exit("10bis login expired. Run: tenbis-credit login")
    except (TenbisError, ValueError) as e:
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
