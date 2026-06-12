"""Securely hand a new prepaid card to the grocery agent.

Run in Terminal:  .venv/bin/python scripts/add_card.py
Prompts locally (card number/CVV don't echo), validates, stores in the macOS keyring.
Then tell the agent: "switch HEB to my new card" — it types the card into heb.com and
deletes the keyring entry on success. Nothing touches chat transcripts or log files."""

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from heb_checkout import cards  # noqa: E402

print("Add a card to the grocery agent's vault (held only until saved on heb.com).")
number = getpass("Card number (hidden): ")
expiry = input("Expiry (MM/YY): ").strip()
cvv = getpass("CVV (hidden): ")
name = input("Name on card (as registered with the issuer): ").strip()
zip_code = input("Billing ZIP (as registered): ").strip()

try:
    saved = cards.store(
        {"number": number, "expiry": expiry, "cvv": cvv, "name": name, "zip": zip_code}
    )
except ValueError as e:
    sys.exit(f"error: {e}")

print(f"\nStored card ending in {saved} in the keyring.")
print('Next: tell the agent "switch HEB to my new card".')
print("Reminder: a store-bought prepaid card must be REGISTERED at the issuer's site")
print("with this name + ZIP first, or heb.com will decline it (AVS).")
