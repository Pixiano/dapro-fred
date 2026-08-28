# Core/scripts/setup_gmail_credentials.py
#
# ONE-TIME setup for the temporary Gmail IMAP bridge (tools/gmail_imap.py)
# — real Gmail API blocked ~1 month on Vatsal's GCP project limit, see
# that module's own docstring. Run by hand: Vatsal types his Gmail
# address and a Google App Password (Google Account -> Security ->
# 2-Step Verification -> App Passwords) here.
#
# CREDENTIAL HANDLING: this data must NEVER be printed back, logged, or
# written to any file — only persisted via `setx` (a real Windows user
# environment variable, the same mechanism the System Properties UI
# uses, and the same os.environ.get() pattern GROQ_API_KEY already uses
# in config/settings.py). getpass.getpass() means the password is never
# echoed to the terminal either, so it never lands in shell history or a
# screen Claude (or anyone else) could read.

import getpass
import subprocess
import sys


def main():
    address = input("Gmail address: ").strip()
    if "@" not in address:
        print("That doesn't look like an email address — aborting, nothing saved.")
        sys.exit(1)

    password = getpass.getpass("Google App Password (will not be shown): ").strip()
    if not password:
        print("No password entered — aborting, nothing saved.")
        sys.exit(1)

    subprocess.run(["setx", "GMAIL_ADDRESS", address], check=True, capture_output=True)
    subprocess.run(["setx", "GMAIL_APP_PASSWORD", password], check=True, capture_output=True)

    print(
        "Saved. This takes effect in NEW terminals/processes only — "
        "restart FRED (or open a new Command Prompt) to pick it up."
    )


if __name__ == "__main__":
    main()
