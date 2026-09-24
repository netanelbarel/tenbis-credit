"""Install the daily run with the OS's own scheduler: launchd, cron or Task Scheduler."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys

from . import config

LABEL = "io.github.tenbis-credit"
CRON_MARKER = "# tenbis-credit"
WINDOWS_TASK = "tenbis-credit"
LAUNCHD_WEEKDAY = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def run_command() -> list[str]:
    """Run through the current interpreter so it works inside pipx/venvs."""
    python = sys.executable
    if sys.platform == "win32":
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = pythonw if os.path.exists(pythonw) else python  # no console window
    return [python, "-m", "tenbis_credit", "run"]


def install(cfg: dict) -> str:
    if sys.platform == "darwin":
        return _launchd_install(cfg)
    if sys.platform == "win32":
        return _windows_install(cfg)
    return _cron_install(cfg)


def remove() -> str:
    if sys.platform == "darwin":
        return _launchd_remove()
    if sys.platform == "win32":
        return _windows_remove()
    return _cron_remove()


def describe(cfg: dict) -> str:
    return f"{'/'.join(d.capitalize() for d in cfg['days'])} at {cfg['time']}"


# ---------- macOS ----------

def launchd_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def launchd_plist(cfg: dict) -> bytes:
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": run_command(),
        # launchd runs a missed job on wake; runner.scheduled_today skips wrong days.
        "StartCalendarInterval": [{"Weekday": LAUNCHD_WEEKDAY[d], "Hour": hh, "Minute": mm}
                                  for d in cfg["days"]],
        "StandardOutPath": os.path.join(config.state_dir(), "scheduler.out.log"),
        "StandardErrorPath": os.path.join(config.state_dir(), "scheduler.err.log"),
    })


def _launchd_install(cfg: dict) -> str:
    os.makedirs(config.state_dir(), exist_ok=True)
    path = launchd_plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _launchd_remove()
    with open(path, "wb") as f:
        f.write(launchd_plist(cfg))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", path], check=True)
    return f"launchd job {LABEL}: {describe(cfg)}"


def _launchd_remove() -> str:
    path = launchd_plist_path()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if os.path.exists(path):
        os.remove(path)
        return f"removed {path}"
    return "no launchd job installed"


# ---------- Linux / other Unix ----------

def cron_line(cfg: dict) -> str:
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    dow = ",".join(str(LAUNCHD_WEEKDAY[d]) for d in cfg["days"])  # cron: 0 = Sunday
    cmd = " ".join(_sh_quote(a) for a in run_command())
    log = _sh_quote(os.path.join(config.state_dir(), "scheduler.log"))
    return f"{mm} {hh} * * {dow} {cmd} >> {log} 2>&1 {CRON_MARKER}"


def _read_crontab() -> list[str]:
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return res.stdout.splitlines() if res.returncode == 0 else []


def _write_crontab(lines: list[str]):
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def _cron_install(cfg: dict) -> str:
    if not shutil.which("crontab"):
        raise RuntimeError("crontab not found; schedule `python -m tenbis_credit run` yourself")
    os.makedirs(config.state_dir(), exist_ok=True)
    lines = [l for l in _read_crontab() if CRON_MARKER not in l] + [cron_line(cfg)]
    _write_crontab(lines)
    return f"cron job: {describe(cfg)}"


def _cron_remove() -> str:
    lines = _read_crontab()
    kept = [l for l in lines if CRON_MARKER not in l]
    if len(kept) == len(lines):
        return "no cron job installed"
    _write_crontab(kept)
    return "removed cron job"


def _sh_quote(s: str) -> str:
    return s if all(c.isalnum() or c in "/._-:" for c in s) else "'" + s.replace("'", "'\\''") + "'"


# ---------- Windows ----------

def windows_create_args(cfg: dict) -> list[str]:
    days = ",".join(d.upper() for d in cfg["days"])
    tr = subprocess.list2cmdline(run_command())
    return ["schtasks", "/Create", "/F", "/TN", WINDOWS_TASK, "/SC", "WEEKLY",
            "/D", days, "/ST", cfg["time"], "/TR", tr]


def _windows_install(cfg: dict) -> str:
    subprocess.run(windows_create_args(cfg), check=True, capture_output=True)
    return f"Task Scheduler task {WINDOWS_TASK}: {describe(cfg)}"


def _windows_remove() -> str:
    res = subprocess.run(["schtasks", "/Delete", "/F", "/TN", WINDOWS_TASK], capture_output=True)
    return "removed scheduled task" if res.returncode == 0 else "no scheduled task installed"


def is_installed() -> bool:
    if sys.platform == "darwin":
        return os.path.exists(launchd_plist_path())
    if sys.platform == "win32":
        return subprocess.run(["schtasks", "/Query", "/TN", WINDOWS_TASK], capture_output=True).returncode == 0
    return any(CRON_MARKER in l for l in _read_crontab())
