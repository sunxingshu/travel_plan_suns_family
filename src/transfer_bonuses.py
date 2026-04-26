"""
Transfer Bonus Tracker — curated list of CONFIRMED active transfer bonuses
for Bilt Points and Amex Membership Rewards.

Only include deals that have been publicly announced and verified.
Update this file when new promotions are confirmed via The Points Guy,
View from the Wing, One Mile at a Time, or the card issuer's own page.

Set `_LAST_UPDATED` whenever you edit the list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


_LAST_UPDATED = "2026-04-26"


@dataclass
class TransferBonus:
    card_currency: str        # e.g. "Bilt Points", "Amex Membership Rewards"
    partner: str              # e.g. "Wyndham Rewards"
    bonus_pct: int            # e.g. 25 means 25% bonus on transfer
    deadline: date            # last day of the promotion
    note: str = ""            # e.g. "Rent Day only — transfer on the 1st"


def get_active_transfer_bonuses(as_of: date | None = None) -> list[TransferBonus]:
    """Return transfer bonuses that are still active (deadline >= today)."""
    today = as_of or date.today()

    # ── CONFIRMED Promotions Only ───────────────────────────────────────
    # Sources: The Points Guy, View from the Wing, One Mile at a Time
    #
    # ⚠️  Do NOT add speculative or unannounced bonuses.
    #     Bilt Rent Day bonuses are announced ~1 day before the 1st.
    #     Amex bonuses are often targeted — only list public ones.
    _ALL_BONUSES: list[TransferBonus] = [
        # --- Bilt (April 2026 Rent Day was Wyndham) ---
        # May 2026 Rent Day not yet announced — check on April 30.
        # Previous months are kept commented out for reference.

        # --- Amex MR ---
        # No public transfer bonuses active as of April 26, 2026.
        # Amex-Etihad partnership ending June 30, 2026 — transfer before then.
        TransferBonus(
            card_currency="Amex Membership Rewards",
            partner="Etihad Guest",
            bonus_pct=0,
            deadline=date(2026, 6, 30),
            note="⚠️ Partnership ending Jun 30 — last chance to transfer",
        ),
    ]

    return [b for b in _ALL_BONUSES if b.deadline >= today]


# ── Tips that are always relevant (no expiration) ──────────────────────
EVERGREEN_TIPS: list[str] = [
    "🏠 Bilt Rent Day is the 1st of every month — bonus transfers announced ~24h before",
    "💳 Amex bonuses are often targeted — check your app under Transfer Points → partner",
    "✈️ Bilt → Hyatt is always 1:1 — one of the best hotel transfer values",
    "🌏 Bilt → Turkish Miles&Smiles 1:1 — great for Star Alliance awards to Asia/Europe",
]
