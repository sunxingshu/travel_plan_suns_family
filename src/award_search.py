import logging
import os
import requests

from src.models import AwardAvailability, FamilyConfig

logger = logging.getLogger(__name__)

_SEATS_AERO_URL = "https://seats.aero/partnerapi/search"
_TIMEOUT = 15


def get_award_availability(
    config: FamilyConfig,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[AwardAvailability]:
    """
    Returns award seat availability for all loyalty programs in config.
    Provider selected by AWARD_PROVIDER env var (default: award_flight_daily).
    Always returns a list (empty on failure — non-blocking).
    """
    provider = os.environ.get("AWARD_PROVIDER", "award_flight_daily").lower()
    programs = [a.loyalty_program for a in config.airlines]

    try:
        if provider == "seats_aero":
            return _seats_aero_search(origin, destination, departure_date, return_date, programs)
        else:
            return _award_flight_daily_search(origin, destination, departure_date, return_date, programs)
    except Exception as e:
        logger.warning("Award search failed (%s→%s): %s", origin, destination, e)
        return []


def _award_flight_daily_search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    programs: list[str],
) -> list[AwardAvailability]:
    """
    Award Flight Daily exposes a free MCP server.
    For HTTP-based access, we use their publicly documented endpoint.
    Returns empty list if the service is unavailable (free tier may have limits).
    """
    base_url = os.environ.get("AWARD_FLIGHT_DAILY_URL", "https://awardflight.daily/api/search")
    api_key = os.environ.get("AWARD_FLIGHT_DAILY_KEY", "")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(
            base_url,
            headers=headers,
            params={
                "origin": origin,
                "destination": destination,
                "date": departure_date,
                "programs": ",".join(programs),
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401:
            logger.warning("Award Flight Daily: API key required. Set AWARD_FLIGHT_DAILY_KEY.")
            return []
        if not resp.ok:
            logger.warning("Award Flight Daily HTTP %s", resp.status_code)
            return []

        results = []
        for item in resp.json().get("results", []):
            results.append(AwardAvailability(
                program=item.get("program", ""),
                origin=origin,
                destination=destination,
                travel_date=departure_date,
                seats_available=int(item.get("seats", 0)),
                cabin=item.get("cabin", "economy"),
                points_required=int(item.get("miles", 0)),
                provider="award_flight_daily",
            ))
        return results
    except Exception as e:
        logger.warning("Award Flight Daily search failed: %s", e)
        return []


def _seats_aero_search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    programs: list[str],
) -> list[AwardAvailability]:
    api_key = os.environ.get("SEATS_AERO_API_KEY", "")
    if not api_key:
        logger.warning("SEATS_AERO_API_KEY not set — skipping award search")
        return []

    # Seats.aero uses source codes, not full program names
    # Map common program names to Seats.aero source codes
    _PROGRAM_TO_SOURCE = {
        "United MileagePlus":      "united",
        "Delta SkyMiles":          "delta",
        "American AAdvantage":     "american",
        "Air Canada Aeroplan":     "aeroplan",
        "Alaska Mileage Plan":     "alaska",
        "Southwest Rapid Rewards": "southwest",
        "JetBlue TrueBlue":        "jetblue",
    }

    results = []
    for program in programs:
        source = _PROGRAM_TO_SOURCE.get(program)
        if not source:
            continue
        try:
            resp = requests.get(
                _SEATS_AERO_URL,
                headers={"Partner-Authorization": api_key},
                params={
                    "origin_airport": origin,
                    "destination_airport": destination,
                    "start_date": departure_date,
                    "end_date": departure_date,
                    "cabin": "economy",
                    "take": 5,
                    "source": source,
                },
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                continue
            for item in resp.json().get("data", []):
                availability = item.get("YAvailable") or item.get("WAvailable") or False
                if not availability:
                    continue
                results.append(AwardAvailability(
                    program=program,
                    origin=origin,
                    destination=destination,
                    travel_date=departure_date,
                    seats_available=int(item.get("YRemainingSeats", 1)),
                    cabin="economy",
                    points_required=int(item.get("YMileageCost", 0) or 0),
                    provider="seats_aero",
                ))
        except Exception as e:
            logger.warning("Seats.aero search failed for %s: %s", program, e)

    return results
