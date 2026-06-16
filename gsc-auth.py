#!/usr/bin/env python3
"""
One-time script to get a Google Search Console refresh token.
Opens your browser, you log in, and it prints the refresh token.

Usage:
    uv run seo/gsc-auth.py
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-auth-oauthlib",
#     "python-dotenv",
# ]
# ///

import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).parent.parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

client_config = {
    "installed": {
        "client_id": os.getenv("GSC_CLIENT_ID"),
        "client_secret": os.getenv("GSC_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

print("Opening browser for Google login...")
flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
creds = flow.run_local_server(port=0, open_browser=True)

print()
print("Done! Update your .env with:")
print()
print(f"GSC_REFRESH_TOKEN={creds.refresh_token}")
print(f"GSC_ACCESS_TOKEN=")
print()
print("The access token will auto-refresh from now on.")
