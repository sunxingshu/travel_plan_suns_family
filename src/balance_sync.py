"""
AwardWallet balance sync — fetches live loyalty account balances and updates FamilyConfig.

How it works:
  1. You connect your airline, hotel, AND credit card accounts to AwardWallet (free account).
  2. AwardWallet tracks all balances in one place (600+ programs).
  3. Each Monday before the travel planner runs, this module calls the AwardWallet API
     with your personal token and overwrites the YAML balances with live values.

Setup (one-time):
  1. Create a free account at awardwallet.com
  2. Connect all your loyalty accounts (airlines, hotels, credit cards)
  3. Go to awardwallet.com/api → generate a Personal API Token
  4. Add it as GitHub Secret: AWARDWALLET_TOKEN

Cost: AwardWallet Plus is $49.99/year — strongly recommended as it enables API access
and tracks more programs. The free tier may have limited API access.

This module is fully non-blocking. If AwardWallet is unavailable or the token is missing,
the pipeline continues with balances from family_preferences.yaml.
"""

from __future__ import annotations

import logging
import os
import requests

from src.models import FamilyConfig

logger = logging.getLogger(__name__)

_API_BASE = "https://awardwallet.com/api/v1"
_TIMEOUT = 15

# Map AwardWallet program names → our config program names
# AwardWallet uses varied naming; this normalises common mismatches.
_PROGRAM_NAME_MAP: dict[str, str] = {
    # Airlines
    "united mileageplus":           "United MileagePlus",
    "mileageplus":                  "United MileagePlus",
    "delta skymiles":               "Delta SkyMiles",
    "skymiles":                     "Delta SkyMiles",
    "american aadvantage":          "American AAdvantage",
    "aadvantage":                   "American AAdvantage",
    "southwest rapid rewards":      "Southwest Rapid Rewards",
    "rapid rewards":                "Southwest Rapid Rewards",
    "alaska mileage plan":          "Alaska Mileage Plan",
    "mileage plan":                 "Alaska Mileage Plan",
    "jetblue trueblue":             "JetBlue TrueBlue",
    "trueblue":                     "JetBlue TrueBlue",
    "british airways executive club": "British Airways Avios",
    "executive club":               "British Airways Avios",
    "air canada aeroplan":          "Air Canada Aeroplan",
    "aeroplan":                     "Air Canada Aeroplan",
    "air france klm flying blue":   "Flying Blue (Air France/KLM)",
    "flying blue":                  "Flying Blue (Air France/KLM)",
    "virgin atlantic flying club":  "Virgin Atlantic Flying Club",
    "flying club":                  "Virgin Atlantic Flying Club",
    "singapore airlines krisflyer": "Singapore KrisFlyer",
    "krisflyer":                    "Singapore KrisFlyer",
    "cathay pacific asia miles":    "Cathay Pacific Asia Miles",
    "asia miles":                   "Cathay Pacific Asia Miles",
    "turkish airlines miles&smiles": "Turkish Miles&Smiles",
    "miles&smiles":                 "Turkish Miles&Smiles",
    # Hotels
    "world of hyatt":               "World of Hyatt",
    "hyatt":                        "World of Hyatt",
    "marriott bonvoy":              "Marriott Bonvoy",
    "bonvoy":                       "Marriott Bonvoy",
    "hilton honors":                "Hilton Honors",
    "honors":                       "Hilton Honors",
    "ihg one rewards":              "IHG One Rewards",
    "ihg rewards club":             "IHG One Rewards",
    "wyndham rewards":              "Wyndham Rewards",
    # Credit cards
    "chase ultimate rewards":       "Chase Ultimate Rewards",
    "ultimate rewards":             "Chase Ultimate Rewards",
    "american express membership rewards": "Amex Membership Rewards",
    "membership rewards":           "Amex Membership Rewards",
    "capital one venture":          "Capital One Venture Miles",
    "venture miles":                "Capital One Venture Miles",
    "citi thankyou":                "Citi ThankYou Points",
    "thankyou points":              "Citi ThankYou Points",
    "bilt rewards":                 "Bilt Points",
    "bilt":                         "Bilt Points",
    "wells fargo autograph":        "Wells Fargo Autograph Rewards",
    "autograph rewards":            "Wells Fargo Autograph Rewards",
}


def sync_balances(config: FamilyConfig) -> tuple[int, list[str]]:
    """
    Fetch live balances from AwardWallet and update config in-place.
    Returns (accounts_updated, list_of_messages).
    Safe to call even if AWARDWALLET_TOKEN is not set — silently skips.
    """
    token = os.environ.get("AWARDWALLET_TOKEN", "")
    if not token:
        return 0, []

    try:
        accounts = _fetch_accounts(token)
    except Exception as e:
        logger.warning("AwardWallet fetch failed: %s", e)
        return 0, [f"AwardWallet sync failed: {e}"]

    updated = 0
    messages: list[str] = []

    for account in accounts:
        program_raw = str(account.get("programName") or account.get("program") or "").lower().strip()
        balance_raw = account.get("balance") or account.get("points") or 0
        try:
            balance = int(float(str(balance_raw).replace(",", "")))
        except (ValueError, TypeError):
            continue

        # Normalise program name
        program = _PROGRAM_NAME_MAP.get(program_raw)
        if program is None:
            # Try partial match
            for key, val in _PROGRAM_NAME_MAP.items():
                if key in program_raw or program_raw in key:
                    program = val
                    break

        if program is None:
            continue

        # Update airline balances
        for airline in config.airlines:
            if airline.loyalty_program == program:
                old = airline.points_balance
                airline.points_balance = balance
                if old != balance:
                    messages.append(f"{program}: {old:,} → {balance:,} pts (AwardWallet live)")
                    updated += 1

        # Update hotel balances
        for brand in config.hotel_brands:
            if brand.loyalty_program == program:
                old = brand.points_balance
                brand.points_balance = balance
                if old != balance:
                    messages.append(f"{program}: {old:,} → {balance:,} pts (AwardWallet live)")
                    updated += 1

        # Update credit card balances
        for card in config.credit_cards:
            if card.currency == program:
                old = card.points_balance
                card.points_balance = balance
                if old != balance:
                    messages.append(f"{card.name} ({program}): {old:,} → {balance:,} pts (AwardWallet live)")
                    updated += 1
                card.awardwallet_account_id = str(account.get("accountId", ""))

    logger.info("AwardWallet sync: %d account(s) updated", updated)
    return updated, messages


def _fetch_accounts(token: str) -> list[dict]:
    """
    Fetch all loyalty accounts from AwardWallet API.
    AwardWallet supports both OAuth2 Bearer tokens and personal API tokens.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    resp = requests.get(
        f"{_API_BASE}/accounts",
        headers=headers,
        timeout=_TIMEOUT,
    )

    if resp.status_code == 401:
        raise RuntimeError(
            "AwardWallet authentication failed. "
            "Verify AWARDWALLET_TOKEN is a valid personal API token from awardwallet.com/api"
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "AwardWallet API access denied. "
            "AwardWallet Plus ($49.99/year) may be required for API access."
        )
    if not resp.ok:
        raise RuntimeError(f"AwardWallet HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    # AwardWallet returns accounts under various keys depending on API version
    if isinstance(data, list):
        return data
    return data.get("accounts", data.get("data", []))
