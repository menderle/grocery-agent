"""Local card vault: one pending card held in the macOS keyring between
`scripts/add_card.py` and the wallet swap on heb.com. The entry is deleted the moment
the card is saved on HEB — the secret lives minutes, not forever. Full card numbers
must never leave this module except into the heb.com form."""

import json

import keyring
import keyring.errors

SERVICE = "grocery-agent"
ENTRY = "pending-card"


def luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def store(card: dict) -> str:
    """Store a pending card; returns its last4 for confirmation messages."""
    number = "".join(c for c in card["number"] if c.isdigit())
    if not luhn_ok(number):
        raise ValueError("card number failed checksum — check for typos")
    card = {**card, "number": number}
    keyring.set_password(SERVICE, ENTRY, json.dumps(card))
    return number[-4:]


def fetch() -> dict | None:
    raw = keyring.get_password(SERVICE, ENTRY)
    return json.loads(raw) if raw else None


def delete() -> None:
    try:
        keyring.delete_password(SERVICE, ENTRY)
    except keyring.errors.PasswordDeleteError:
        pass


def last4(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else "????"
