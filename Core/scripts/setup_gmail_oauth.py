# Core/scripts/setup_gmail_oauth.py
#
# One-time browser consent for tools/gmail_api.py. Opens Google's sign-in
# page, catches the redirect on a random localhost port, and writes the
# token next to the client file (outside every repo). Re-run if the token
# is ever revoked or expires (7 days while the consent screen is "Testing").

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from google_auth_oauthlib.flow import InstalledAppFlow

from config.settings import GMAIL_OAUTH_CLIENT_PATH, GMAIL_OAUTH_TOKEN_PATH
from tools.gmail_api import SCOPES

if __name__ == "__main__":
    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_OAUTH_CLIENT_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    GMAIL_OAUTH_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved token to {GMAIL_OAUTH_TOKEN_PATH}")
