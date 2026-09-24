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
  Card …1234 (daily budget 100 ₪): 35 ₪ available to move to Credit
    Will be moved every scheduled evening.
Mode: 'auto' or 'monthly' [auto]:
Work days [sun,mon,tue,wed,thu]:
Time (HH:MM, 24h) [22:00]:
✓ launchd job io.github.tenbis-credit: Sun/Mon/Tue/Wed/Thu at 22:00
Move today's leftover now? (y/n) [y]:
✓ Moved 35 ₪ from card …1234 to 10bis Credit
```

📖 **Full guide:** [the wiki](https://github.com/netanelbarel/tenbis-credit/wiki) covers setup, budget types, settings, troubleshooting and security. [מדריך בעברית](https://github.com/netanelbarel/tenbis-credit/wiki/%D7%9E%D7%93%D7%A8%D7%99%D7%9A-%D7%91%D7%A2%D7%91%D7%A8%D7%99%D7%AA).

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
3. It reads how much each card may move to Credit (`availableAmount`), and moves all of
   it, up to `max_amount_per_run`, when that card's budget is about to reset (below).
4. You get a desktop notification for every move, and if the login expired.

### Every company sets budgets differently

The amount and the budget type come from 10bis for each card, as your employer set them:

| Your card's budget | When it's moved (mode `auto`) |
|---|---|
| Daily limit (e.g. 100 ₪ a day) | Every scheduled evening |
| Weekly limit, no daily limit | On the last work day of the week (Thursday by default) |
| Monthly budget only | On the last work day of the month |

A monthly or weekly budget is never moved early, since that would leave nothing for
meals. Not even `run --force` does it. Setup and `status` show what was detected for each
card. If your company offers **10bis's own automatic credit** for a card, setup says so:
turning that on in the app is the official way. Cards that already have it on are left alone.

Scheduling uses the OS's own scheduler: **launchd** on macOS, **cron** on Linux and
**Task Scheduler** on Windows. There's no background process of its own.

## Settings

`config.json` in `~/.config/tenbis-credit/` (Windows: `%APPDATA%\tenbis-credit\`):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `auto` | `auto`: each card by its budget type (above). `monthly`: every card only on the last scheduled day of the month. (`daily` from 0.1.x means `auto`.) |
| `days` | `sun`…`thu` | Days to run (`mon tue wed thu fri sat sun`) |
| `time` | `22:00` | Local time to run. Keep it before midnight |
| `max_amount_per_run` | `1000` | Safety cap per card per run, in ₪ |
| `min_amount` | `1` | Skip when less than this is available |
| `notify` | `true` | Desktop notifications |
| `endpoints` | `{}` | Override a 10bis URL if 10bis moves one (see `api.py`) |

After editing `days` or `time`, run `tenbis-credit schedule install` again.
Run history: `~/.local/state/tenbis-credit/log.jsonl` (Windows: `%LOCALAPPDATA%\tenbis-credit\`).

## בעברית בקצרה

הכלי מעביר את יתרת התקציב בתן ביס לתן ביס קרדיט, כדי שהיתרה לא תאבד. תקציב יומי מועבר כל ערב (א׳–ה׳, 22:00), תקציב שבועי ביום העבודה האחרון בשבוע, ותקציב חודשי ביום העבודה האחרון בחודש, לפי מה שהמעסיק שלך הגדיר.
התקנה: `pipx install tenbis-credit` ואז `tenbis-credit setup` — מתחברים עם הקוד שתן ביס שולחת, וזהו.
הכלי רץ על המחשב שלך בלבד, ופרטי ההתחברות לא נשלחים לשום מקום מלבד תן ביס. לא רשמי ולא קשור לתן ביס.

## Development

```
python -m unittest discover -s tests      # no dependencies needed
```

## License

MIT
