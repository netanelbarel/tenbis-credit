# tenbis-credit

Moves your unused **10bis** meal budget into **10bis Credit** every work day, so leftover
budget carries over instead of expiring. It does what the "טען לקרדיט" (load to credit)
button in 10bis does, on a schedule, on your own computer.

```
$ pipx install tenbis-credit
$ tenbis-credit setup
Your 10bis email: dana@example.com
Enter the code 10bis sent you by SMS: 48213
✓ Logged in as dana@example.com
  Card …1234: 35 ₪ available to move to Credit
Mode: 'daily' (every work day) or 'monthly' (last work day only) [daily]:
Days [sun,mon,tue,wed,thu]:
Time (HH:MM, 24h) [22:00]:
✓ launchd job io.github.tenbis-credit: Sun/Mon/Tue/Wed/Thu at 22:00
Move today's leftover now? (y/n) [y]:
✓ Moved 35 ₪ from card …1234 to 10bis Credit
```

> **Unofficial.** Not made by or affiliated with 10bis. 10bis has no public API: this tool
> uses the same private endpoints the 10bis website uses, and 10bis can change them at any
> time. Use it only with your own account, at your own risk.

## Requirements

- Your employer has to allow moving budget to 10bis Credit. If you see a "load to credit"
  option under your card in the 10bis app, you're good.
- Python 3.9+ on macOS, Windows or Linux, and a computer that's on (or wakes) in the
  evening. Budgets reset at midnight, so a day the computer sleeps through is not moved.

## Install

```
pipx install tenbis-credit      # or: pip install --user tenbis-credit
tenbis-credit setup
```

No pipx? macOS: `brew install pipx`, Windows: `py -m pip install --user pipx`.
From source: `pipx install git+https://github.com/netanelbarel/tenbis-credit`.

## Commands

| Command | What it does |
|---|---|
| `tenbis-credit setup` | Guided login, schedule install, and an optional first move |
| `tenbis-credit status` | Login state, schedule, today's available amount, last runs |
| `tenbis-credit run [--dry-run] [--force]` | Move the leftover now (what the schedule runs) |
| `tenbis-credit login` | Log in again (only needed if the computer was off for months) |
| `tenbis-credit schedule install\|remove` | Change or remove the scheduled run |
| `tenbis-credit uninstall` | Remove the schedule, your saved login and settings |

## How it works

1. **Login** is the same one-time code 10bis sends by SMS or email. The resulting session
   cookies are saved in your profile, readable only by you, and never sent anywhere but 10bis.
2. **Every run** first renews the session (as the website does), so you don't have to log
   in again as long as it runs regularly.
3. It reads how much each card may move to Credit today (`availableAmount`), and moves
   all of it, up to `max_amount_per_run`.
4. You get a desktop notification for every move, and if the login expired.

Scheduling uses the OS's own scheduler: **launchd** on macOS, **cron** on Linux and
**Task Scheduler** on Windows. There's no background process of its own.

## Settings

`config.json` in `~/.config/tenbis-credit/` (Windows: `%APPDATA%\tenbis-credit\`):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `daily` | `daily`: every scheduled day. `monthly`: only the last scheduled day of the month |
| `days` | `sun`…`thu` | Days to run (`mon tue wed thu fri sat sun`) |
| `time` | `22:00` | Local time to run. Keep it before midnight |
| `max_amount_per_run` | `1000` | Safety cap per card per run, in ₪ |
| `min_amount` | `1` | Skip when less than this is available |
| `notify` | `true` | Desktop notifications |
| `endpoints` | `{}` | Override a 10bis URL if 10bis moves one (see `api.py`) |

After editing `days` or `time`, run `tenbis-credit schedule install` again.
Run history: `~/.local/state/tenbis-credit/log.jsonl` (Windows: `%LOCALAPPDATA%\tenbis-credit\`).

## בעברית בקצרה

הכלי מעביר כל ערב (א׳–ה׳, 22:00) את יתרת התקציב היומית בתן ביס לתן ביס קרדיט, כדי שהיתרה לא תאבד.
התקנה: `pipx install tenbis-credit` ואז `tenbis-credit setup` — מתחברים עם הקוד שתן ביס שולחת, וזהו.
הכלי רץ על המחשב שלך בלבד, ופרטי ההתחברות לא נשלחים לשום מקום מלבד תן ביס. לא רשמי ולא קשור לתן ביס.

## Development

```
python -m unittest discover -s tests      # no dependencies needed
```

## License

MIT
