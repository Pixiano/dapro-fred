"""Windows toast notification with no extra package: PowerShell and the built-in Windows.UI.Notifications API.

Sockets
  in : show(title, text, runner=None) -> bool        True when the toast was handed to Windows; False (never raises) on any failure
       runner: tests replace the subprocess call with fn(env: dict) -> int (exit code)
  out: a toast in the Windows notification area. Title and text travel in environment variables, never inside the command line, so no text can become a command.
Self-check: python ui/toast.py (fake runner; no toast is shown)
"""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import log

APP_ID = "FRED.Assistant"          # the toast's app identity: Windows shows its DisplayName ("FRED") as the sender; registered under HKCU, current user only
SCRIPT = (
    f"$k = 'HKCU:\\Software\\Classes\\AppUserModelId\\{APP_ID}'; if (-not (Test-Path $k)) {{ New-Item -Path $k -Force > $null }}; Set-ItemProperty -Path $k -Name DisplayName -Value 'FRED';"
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "$t.Item(0).AppendChild($x.CreateTextNode($env:FRED_TOAST_TITLE)) > $null;"
    "$t.Item(1).AppendChild($x.CreateTextNode($env:FRED_TOAST_TEXT)) > $null;"
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def _run(env: dict) -> int:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", SCRIPT], env=env, capture_output=True, timeout=15,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode


def show(title: str, text: str, runner=None) -> bool:
    env = {**os.environ, "FRED_TOAST_TITLE": title[:80], "FRED_TOAST_TEXT": text[:240]}
    try:
        return (runner or _run)(env) == 0
    except Exception as ex:
        log.session("toast_error", error=type(ex).__name__)
        return False


def _selfcheck():
    seen = []
    assert show("Careful", "Fake login page", runner=lambda env: seen.append((env["FRED_TOAST_TITLE"], env["FRED_TOAST_TEXT"])) or 0) and seen == [("Careful", "Fake login page")]
    seen.clear()
    show("x" * 200, "y" * 500, runner=lambda env: seen.append((len(env["FRED_TOAST_TITLE"]), len(env["FRED_TOAST_TEXT"]))) or 0)
    assert seen == [(80, 240)], seen
    seen.clear()
    show("'; calc; '", "$(calc)", runner=lambda env: seen.append(env["FRED_TOAST_TEXT"]) or 0)
    assert seen == ["$(calc)"] and "calc" not in SCRIPT                                                  # text is data in an environment variable, never part of the script
    assert show("a", "b", runner=lambda env: 1) is False
    assert show("a", "b", runner=lambda env: 1 / 0) is False
    print("toast ok")


if __name__ == "__main__":
    _selfcheck()
