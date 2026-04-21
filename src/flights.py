import logging
import os
import re
import requests
from typing import Optional

from src.models import FamilyConfig, FlightOption

logger = logging.getLogger(__name__)

# NOTE: Amadeus self-service API is decommissioned July 17, 2026.
# Default is now Kiwi/Tequila. Amadeus remains available as a legacy option
# via FLIGHT_PROVIDER=amadeus until that date.
_KIWI_URL = "https://api.tequila.kiwi.com/v2/search"
_TIMEOUT = 15


def get_best_flight(
    config: FamilyConfig,
    destination_iata: str,
    date_pairs: list[tuple[str, str]],
    amadeus_client=None,
) -> Optional[FlightOption]:
    """
    Returns the best FlightOption from up to 3 date pairs.
    When prefer_nonstop=True: tries nonstop only first, then falls back to 1-stop.
    Provider selected by FLIGHT_PROVIDER env var (default: kiwi).
    """
    provider = os.environ.get("FLIGHT_PROVIDER", "kiwi").lower()
    candidates: list[FlightOption] = []

    for departure, return_date in date_pairs[:3]:
        try:
            if provider == "amadeus":
                if amadeus_client is None:
                    logger.warning("Amadeus client not provided; falling back to Kiwi")
                    options = _kiwi_search(config, destination_iata, departure, return_date)
                else:
                    options = _amadeus_search(amadeus_client, config, destination_iata, departure, return_date)
            else:
                options = _kiwi_search(config, destination_iata, departure, return_date)

            valid = [f for f in options if f.duration_hours <= config.max_flight_hours]
            candidates.extend(valid)
        except FlightSearchError as e:
            logger.warning("Flight search failed (%s→%s, %s): %s", config.home_airport, destination_iata, departure, e)

    if not candidates:
        return None

    if config.prefer_nonstop:
        nonstop = [f for f in candidates if f.stops == 0]
        pool = nonstop if nonstop else candidates  # fall back to any stops if no nonstop found
        if not nonstop:
            logger.info("No nonstop flights found for %s — using best with stops", destination_iata)
    else:
        pool = candidates

    return min(pool, key=lambda f: (f.stops, f.price_usd))


def _amadeus_search(
    amadeus_client,
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    from amadeus import ResponseError
    try:
        params = dict(
            originLocationCode=config.home_airport,
            destinationLocationCode=destination,
            departureDate=departure_date,
            returnDate=return_date,
            adults=config.adults,
            currencyCode="USD",
            max=10,
        )
        if config.children_ages:
            params["children"] = len(config.children_ages)

        resp = amadeus_client.shopping.flight_offers_search.get(**params)
        return [_parse_amadeus_offer(offer, config.home_airport, destination) for offer in resp.data]
    except ResponseError as e:
        raise FlightSearchError(f"Amadeus error: {e}") from e
    except Exception as e:
        raise FlightSearchError(f"Amadeus unexpected: {e}") from e


def _kiwi_search(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    api_key = os.environ.get("KIWI_API_KEY", "")
    if not api_key:
        raise FlightSearchError("KIWI_API_KEY not set — register at tequila.kiwi.com")

    # Kiwi uses max_stopovers=0 for nonstop; we fetch both and filter in caller
    try:
        resp = requests.get(
            _KIWI_URL,
            headers={"apikey": api_key},
            params={
                "fly_from": config.home_airport,
                "fly_to": destination,
                "date_from": _reformat_date(departure_date),
                "date_to": _reformat_date(departure_date),
                "return_from": _reformat_date(return_date),
                "return_to": _reformat_date(return_date),
                "adults": config.adults,
                "children": len(config.children_ages),
                "curr": "USD",
                "limit": 15,
                "max_stopovers": 1,   # fetch up to 1-stop; nonstop preference applied in caller
            },
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            raise FlightSearchError(f"Kiwi HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return [_parse_kiwi_offer(item, config.home_airport, destination) for item in data.get("data", [])]
    except FlightSearchError:
        raise
    except Exception as e:
        raise FlightSearchError(f"Kiwi unexpected: {e}") from e


def _parse_amadeus_offer(offer: dict, origin: str, destination: str) -> FlightOption:
    price = float(offer["price"]["grandTotal"])
    itinerary = offer["itineraries"][0]
    segments = itinerary["segments"]
    airline_iata = segments[0]["carrierCode"]
    stops = len(segments) - 1
    duration_hours = _iso_duration_to_hours(itinerary["duration"])
    departure_date = segments[0]["departure"]["at"][:10]
    last_seg = offer["itineraries"][-1]["segments"]
    return_date = last_seg[-1]["arrival"]["at"][:10]

    return FlightOption(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        airline=airline_iata,
        airline_iata=airline_iata,
        price_usd=price,
        duration_hours=duration_hours,
        stops=stops,
        provider="amadeus",
    )


def _parse_kiwi_offer(item: dict, origin: str, destination: str) -> FlightOption:
    price = float(item.get("price", 0))
    duration_s = item.get("duration", {}).get("departure", 0)
    duration_hours = duration_s / 3600 if duration_s else 0.0
    routes = item.get("route", [])
    airline_iata = routes[0].get("airline", "") if routes else ""
    stops = max(0, len(routes) - 1)
    departure_date = item.get("local_departure", "")[:10]
    return_date = item.get("local_arrival", "")[:10]

    return FlightOption(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        airline=airline_iata,
        airline_iata=airline_iata,
        price_usd=price,
        duration_hours=round(duration_hours, 1),
        stops=stops,
        provider="kiwi",
    )


def _iso_duration_to_hours(duration: str) -> float:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return round(hours + minutes / 60, 2)


def _reformat_date(iso_date: str) -> str:
    """Convert YYYY-MM-DD to DD/MM/YYYY for Kiwi API."""
    parts = iso_date.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


class FlightSearchError(Exception):
    pass
