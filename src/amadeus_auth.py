"""
Amadeus Self-Service API — OAuth2 token management.

The Amadeus API requires a client-credentials OAuth2 flow:
  1. POST client_id + client_secret → receive access_token (valid ~30 min)
  2. Attach access_token as Bearer header to all API calls

This module caches the token and refreshes it automatically.

Requires env vars:
  AMADEUS_API_KEY     — your API Key (client_id)
  AMADEUS_API_SECRET  — your API Secret (client_secret)

Docs: https://developers.amadeus.com/self-service/apis-docs/guides/authorization
"""
import logging
import os
import time
import requests

logger = logging.getLogger(__name__)

_TOKEN_URL_TEST = "https://test.api.amadeus.com/v1/security/oauth2/token"
_TOKEN_URL_PROD = "https://api.amadeus.com/v1/security/oauth2/token"

_BASE_URL_TEST = "https://test.api.amadeus.com"
_BASE_URL_PROD = "https://api.amadeus.com"

# Module-level cache
_cached_token: str = ""
_token_expires_at: float = 0.0


def is_configured() -> bool:
    """Return True if Amadeus API credentials are present."""
    return bool(
        os.environ.get("AMADEUS_API_KEY", "").strip()
        and os.environ.get("AMADEUS_API_SECRET", "").strip()
    )


def get_base_url() -> str:
    """Return the appropriate base URL (test vs production)."""
    env = os.environ.get("AMADEUS_ENV", "test").lower()
    return _BASE_URL_PROD if env == "production" else _BASE_URL_TEST


def get_access_token() -> str:
    """
    Return a valid Amadeus access token, refreshing if expired.
    Raises AmadeusAuthError on failure.
    """
    global _cached_token, _token_expires_at

    # Return cached token if still valid (with 60s buffer)
    if _cached_token and time.time() < (_token_expires_at - 60):
        return _cached_token

    api_key = os.environ.get("AMADEUS_API_KEY", "").strip()
    api_secret = os.environ.get("AMADEUS_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise AmadeusAuthError(
            "AMADEUS_API_KEY and AMADEUS_API_SECRET must be set. "
            "Get free credentials at https://developers.amadeus.com"
        )

    env = os.environ.get("AMADEUS_ENV", "test").lower()
    token_url = _TOKEN_URL_PROD if env == "production" else _TOKEN_URL_TEST

    try:
        resp = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": api_secret,
            },
            timeout=10,
        )
        if resp.status_code == 401:
            raise AmadeusAuthError(
                "Amadeus credentials invalid. Check AMADEUS_API_KEY and AMADEUS_API_SECRET."
            )
        if not resp.ok:
            raise AmadeusAuthError(f"Amadeus token request failed HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        _cached_token = data["access_token"]
        expires_in = int(data.get("expires_in", 1799))  # Default ~30 min
        _token_expires_at = time.time() + expires_in
        logger.debug("Amadeus token acquired (expires in %ds, env=%s)", expires_in, env)
        return _cached_token

    except AmadeusAuthError:
        raise
    except Exception as e:
        raise AmadeusAuthError(f"Amadeus auth unexpected error: {e}") from e


class AmadeusAuthError(Exception):
    pass
