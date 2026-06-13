"""Save your HEB login for the agent — locally, never through chat.

Run in Terminal:  .venv/bin/python scripts/save_heb_login.py
Stores email+password in the macOS Keychain under texas-grocery-mcp's own service,
exactly as its session_save_credentials tool would; session_refresh then logs in
automatically whenever the session expires."""

import sys
from getpass import getpass
from pathlib import Path

from texas_grocery_mcp.auth.credentials import CredentialStore
from texas_grocery_mcp.utils.config import get_settings

email = input("HEB account email: ").strip()
password = getpass("HEB password (hidden): ")
if not email or not password:
    sys.exit("error: both email and password are required")

auth_dir = Path(get_settings().auth_state_path).expanduser().parent
auth_dir.mkdir(parents=True, exist_ok=True)
result = CredentialStore(auth_dir).save(email, password)
print(f"\nSaved: {result}")
print('Next: tell the agent "refresh my HEB session" — it logs in automatically.')
