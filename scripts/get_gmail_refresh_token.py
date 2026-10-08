"""Run once, locally, to get the GMAIL_REFRESH_TOKEN for .env.

1. In Google Cloud Console (same project as Google login):
   - APIs & Services -> Library -> enable "Gmail API"
   - OAuth consent screen -> add the scope .../auth/gmail.send
     and set Publishing status to "In production"
     (in "Testing", refresh tokens stop working after 7 days)
   - Credentials -> Create credentials -> OAuth client ID -> "Desktop app"
2. Put that client's ID and secret in .env as GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET
3. Run from the backend folder:  python scripts/get_gmail_refresh_token.py
4. A browser opens: sign in with the Gmail account that should SEND the
   notifications and allow access
5. Copy the printed refresh token into .env as GMAIL_REFRESH_TOKEN, and set
   GMAIL_SENDER to that same Gmail address
"""

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main():

    client_config = {
        "installed": {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    # access_type=offline + prompt=consent makes Google return a refresh token
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    print("\nGMAIL_REFRESH_TOKEN=" + creds.refresh_token)


if __name__ == "__main__":
    main()
