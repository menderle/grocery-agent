"""favor_checkout: on-demand H-E-B grocery delivery via Favor (favordelivery.com).

Favor is H-E-B-owned; "H-E-B Now" (~20-45 min) and "Express" (~2h) deliver up to 25
items. There's no on-demand slot inside heb.com checkout, so this is a separate module
mirroring heb_checkout — its own parked Chrome session (favordelivery.com, separate Favor
account) and selectors, but it REUSES the shared policy / approvals / audit engine so the
same spend limits, approval modes, and money-safety guards apply.

Status: browse/search/cart flow verified live against favordelivery.com; the final
logged-in place-order step is best-effort and unverified — defaults to dry-run until a
real Favor login confirms the checkout selectors (same path HEB checkout took).
"""

__version__ = "0.1.0"
