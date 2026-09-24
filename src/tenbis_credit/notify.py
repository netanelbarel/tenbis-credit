"""Best-effort desktop notifications. Failing to notify never fails a run."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

TITLE = "10bis → Credit"


def notify(message: str):
    try:
        if sys.platform == "darwin":
            script = f"display notification {json.dumps(message)} with title {json.dumps(TITLE)}"
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        elif sys.platform == "win32":
            _windows_toast(message)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", TITLE, message], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _windows_toast(message: str):
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        f"$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode({_ps(TITLE)})) > $null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode({_ps(message)})) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('tenbis-credit')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=15)


def _ps(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
