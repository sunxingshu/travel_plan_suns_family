import logging
import os
import re
import requests
from typing import Optional

from src.models import FamilyConfig, FlightOption

logger = logging.getLogger(__name__)

# Travelpayouts Aviasales Data API — same token as hotels, already in TRAVELPAYOUTS_TOKEN.
# Returns cached prices (24-48h old) — accurate enough for weekly planning.
# Docs: support.travelpayouts.com/hc/en-us/articles/203956163
_TP_FLIGHT_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

# Kiwi/Tequila — kept as Tier 2. Requires KIWI_API_KEY.
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
    When prefer_nonstop=True: nonstop results are preferred; falls back to 1-stop if none found.
    Provider selected by FLIGHT_PROVIDER env var (default: travelpayouts).
    """
    provider = os.environ.get("FLIGHT_PROVIDER", "travelpayouts").lower()
    # SerpAPI overrides provider when key is configured
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        provider = "serpapi"
    candidates: list[FlightOption] = []

    for departure, return_date in date_pairs[:3]:
        try:
            if provider == "serpapi":
                options = _serpapi_search(config, destination_iata, departure, return_date)
            elif provider == "kiwi":
                options = _kiwi_search(config, destination_iata, departure, return_date)
            elif provider == "amadeus":
                if amadeus_client is None:
                    logger.warning("Amadeus client not provided; falling back to Travelpayouts")
                    options = _travelpayouts_search(config, destination_iata, departure, return_date)
                else:
                    options = _amadeus_search(amadeus_client, config, destination_iata, departure, return_date)
            else:
                options = _travelpayouts_search(config, destination_iata, departure, return_date)

            valid = [f for f in options if f.duration_hours <= config.max_flight_hours]
            candidates.extend(valid)
        except FlightSearchError as e:
            logger.warning("Flight search failed (%s→%s, %s): %s", config.home_airport, destination_iata, departure, e)

    # Fallback: cheap prices endpoint has much broader coverage than prices_for_dates
    if not candidates and provider == "travelpayouts":
        try:
            candidates = _travelpayouts_cheap_prices_fallback(config, destination_iata)
            candidates = [f for f in candidates if f.duration_hours <= config.max_flight_hours]
        except Exception as e:
            logger.debug("Cheap prices fallback failed for %s: %s", destination_iata, e)

    if not candidates:
        return None

    if config.prefer_nonstop:
        nonstop = [f for f in candidates if f.stops == 0]
        pool = nonstop if nonstop else candidates
        if not nonstop:
            logger.info("No nonstop found for %s — using best with stops", destination_iata)
    else:
        pool = candidates

    return min(pool, key=lambda f: (f.stops, f.price_usd))


# Popular family-friendly destinations reachable from SFO within 10h flight.
# Used by the SerpAPI broad search when Travelpayouts returns insufficient data.
_POPULAR_FROM_SFO = [
    ("HNL", "Honolulu"),
    ("OGG", "Maui"),
    ("LAS", "Las Vegas"),
    ("MCO", "Orlando"),
    ("MIA", "Miami"),
    ("CUN", "Cancun"),
    ("PVR", "Puerto Vallarta"),
    ("SJD", "Cabo San Lucas"),
    ("YVR", "Vancouver"),
    ("SJO", "San Jose"),
    ("SJU", "San Juan"),
    ("JFK", "New York City"),
    ("SEA", "Seattle"),
    ("DEN", "Denver"),
]


def get_all_cheap_flights(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Broad search: returns cheapest flights from home airport across popular destinations.
    SerpAPI (real-time Google Flights) is used when key is set; Travelpayouts is fallback.
    Returns list sorted by price ascending — AI then selects the best for this family.
    """
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        results = _serpapi_broad_search(config, candidate_windows)
        if results:
            logger.info("SerpAPI broad search: %d destinations priced", len(results))
            return sorted(results, key=lambda f: f.price_usd)
        logger.warning("SerpAPI broad search returned no results — falling back to Travelpayouts")

    return _travelpayouts_broad_search(config, candidate_windows)


def _serpapi_broad_search(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """Run SerpAPI Google Flights for a curated list of popular destinations."""
    from datetime import date, timedelta
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return []

    min_date = (date.today() + timedelta(weeks=2)).isoformat()
    valid_windows = [(dep, ret) for dep, ret in candidate_windows if dep >= min_date]
    if not valid_windows:
        return []
    departure_date, return_date = valid_windows[0]

    children_count = len([a for a in config.children_ages if a >= 2])
    infants = len([a for a in config.children_ages if a < 2])

    results: list[FlightOption] = []
    for iata, _city in _POPULAR_FROM_SFO:
        try:
            params: dict = {
                "engine": "google_flights",
                "departure_id": config.home_airport,
                "arrival_id": iata,
                "outbound_date": departure_date,
                "return_date": return_date,
                "adults": config.adults,
                "currency": "USD",
                "hl": "en",
                "type": "1",
                "api_key": api_key,
            }
            if children_count:
                params["children"] = children_count
            if infants:
                params["infants_on_lap"] = infants
            if config.prefer_nonstop:
                params["stops"] = "1"

            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            if not resp.ok:
                logger.debug("SerpAPI broad HTTP %s for %s", resp.status_code, iata)
                continue
            data = resp.json()

            price_insights = data.get("price_insights", {})
            price_level = price_insights.get("price_level", "")
            is_deal = price_level == "low"

            all_flights = data.get("best_flights", []) + data.get("other_flights", [])
            if not all_flights:
                continue

            best = min(all_flights, key=lambda f: float(f.get("price", 999999)))
            price = float(best.get("price", 0))
            if price <= 0:
                continue

            segs = best.get("flights", [])
            if not segs:
                continue
            total_mins = int(best.get("total_duration", 0))
            airline = segs[0].get("airline", "")
            stops = len(best.get("layovers", []))
            dep_time = segs[0].get("departure_airport", {}).get("time", "")
            dep_date = dep_time[:10] if dep_time else departure_date

            results.append(FlightOption(
                origin=config.home_airport,
                destination=iata,
                departure_date=dep_date,
                return_date=return_date,
                airline=airline,
                airline_iata="",
                price_usd=price,
                duration_hours=round(total_mins / 60, 1),
                stops=stops,
                provider="serpapi",
                is_deal=is_deal,
                price_level=price_level,
            ))
        except Exception as e:
            logger.debug("SerpAPI broad search failed for %s: %s", iata, e)

    return results


def _travelpayouts_broad_search(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """Travelpayouts get_cheap_prices without destination — returns cached prices for 50+ routes."""
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        logger.warning("TRAVELPAYOUTS_TOKEN not set — broad search unavailable")
        return []

    total_passengers = config.adults + len(config.children_ages)
    months_to_search = list(dict.fromkeys(dep[:7] for dep, _ in candidate_windows))[:4]
    best_by_dest: dict[str, FlightOption] = {}

    for month in months_to_search:
        try:
            resp = requests.get(
                "https://api.travelpayouts.com/aviasales/v3/get_cheap_prices",
                params={
                    "origin": config.home_airport,
                    "currency": "usd",
                    "departure_at": month,
                    "token": token,
                    # NOTE: no direct=true here — broad discovery needs all routes
                },
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                logger.warning("Travelpayouts broad search HTTP %s for month %s: %s",
                               resp.status_code, month, resp.text[:200])
                continue
            data = resp.json()
            if not data.get("success"):
                logger.warning("Travelpayouts broad search error for %s: %s", month, data)
                continue

            for dest_iata, item in data.get("data", {}).items():
                price_per_person = float(item.get("price", 0))
                if price_per_person <= 0:
                    continue
                duration_to = int(item.get("duration_to", 0) or 0)
                flight = FlightOption(
                    origin=config.home_airport,
                    destination=dest_iata,
                    departure_date=str(item.get("departure_at", ""))[:10],
                    return_date=str(item.get("return_at", ""))[:10],
                    airline=str(item.get("airline", "")),
                    airline_iata=str(item.get("airline", "")),
                    price_usd=round(price_per_person * total_passengers, 2),
                    duration_hours=round(duration_to / 60, 1) if duration_to else 0.0,
                    stops=int(item.get("transfers", 0)),
                    provider="travelpayouts",
                )
                existing = best_by_dest.get(dest_iata)
                if existing is None or flight.price_usd < existing.price_usd:
                    best_by_dest[dest_iata] = flight

        except Exception as e:
            logger.warning("Travelpayouts broad search failed for month %s: %s", month, e)

    return sorted(best_by_dest.values(), key=lambda f: f.price_usd)


def _travelpayouts_search(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        raise FlightSearchError("TRAVELPAYOUTS_TOKEN not set")

    total_passengers = config.adults + len(config.children_ages)

    try:
        resp = requests.get(
            _TP_FLIGHT_URL,
            params={
                "origin": config.home_airport,
                "destination": destination,
                "departure_at": departure_date,
                "return_at": return_date,
                "direct": "true" if config.prefer_nonstop else "false",
                "currency": "usd",
                "sorting": "price",
                "limit": 10,
                "token": token,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401:
            raise FlightSearchError("Travelpayouts token invalid — check TRAVELPAYOUTS_TOKEN secret")
        if not resp.ok:
            raise FlightSearchError(f"Travelpayouts HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        if not data.get("success"):
            raise FlightSearchError(f"Travelpayouts error: {data.get('error', 'unknown')}")

        results = []
        for item in data.get("data", []):
            price_per_person = float(item.get("price", 0))
            if price_per_person <= 0:
                continue
            total_price = round(price_per_person * total_passengers, 2)
            duration_to = int(item.get("duration_to", item.get("duration", 0)) or 0)
            results.append(FlightOption(
                origin=config.home_airport,
                destination=destination,
                departure_date=str(item.get("departure_at", departure_date))[:10],
                return_date=str(item.get("return_at", return_date))[:10],
                airline=str(item.get("airline", "")),
                airline_iata=str(item.get("airline", "")),
                price_usd=total_price,
                duration_hours=round(duration_to / 60, 1) if duration_to else 0.0,
                stops=int(item.get("transfers", 0)),
                provider="travelpayouts",
            ))

        # If we asked for nonstop and got nothing, retry allowing 1 stop
        if not results and config.prefer_nonstop:
            return _travelpayouts_search_with_stops(config, destination, departure_date, return_date, total_passengers)

        return results
    except FlightSearchError:
        raise
    except Exception as e:
        raise FlightSearchError(f"Travelpayouts unexpected: {e}") from e


def _travelpayouts_cheap_prices_fallback(
    config: FamilyConfig,
    destination: str,
) -> list[FlightOption]:
    """Fallback when prices_for_dates has no cached data for specific dates.
    get_cheap_prices returns the best recently seen price for a route."""
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        return []
    total_passengers = config.adults + len(config.children_ages)
    resp = requests.get(
        "https://api.travelpayouts.com/aviasales/v3/get_cheap_prices",
        params={"origin": config.home_airport, "destination": destination, "currency": "usd", "token": token},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        return []
    data = resp.json()
    if not data.get("success"):
        return []
    item = data.get("data", {}).get(destination)
    if not item:
        return []
    price_per_person = float(item.get("price", 0))
    if price_per_person <= 0:
        return []
    duration_to = int(item.get("duration_to", 0) or 0)
    return [FlightOption(
        origin=config.home_airport,
        destination=destination,
        departure_date=str(item.get("departure_at", ""))[:10],
        return_date=str(item.get("return_at", ""))[:10],
        airline=str(item.get("airline", "")),
        airline_iata=str(item.get("airline", "")),
        price_usd=round(price_per_person * total_passengers, 2),
        duration_hours=round(duration_to / 60, 1) if duration_to else 0.0,
        stops=int(item.get("transfers", 0)),
        provider="travelpayouts",
    )]


def _travelpayouts_search_with_stops(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
    total_passengers: int,
) -> list[FlightOption]:
    """Fallback: retry without the direct=true constraint."""
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    resp = requests.get(
        _TP_FLIGHT_URL,
        params={
            "origin": config.home_airport,
            "destination": destination,
            "departure_at": departure_date,
            "return_at": return_date,
            "direct": "false",
            "currency": "usd",
            "sorting": "price",
            "limit": 10,
            "token": token,
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        return []
    data = resp.json()
    results = []
    for item in data.get("data", []):
        price_per_person = float(item.get("price", 0))
        if price_per_person <= 0:
            continue
        duration_to = int(item.get("duration_to", item.get("duration", 0)) or 0)
        results.append(FlightOption(
            origin=config.home_airport,
            destination=destination,
            departure_date=str(item.get("departure_at", departure_date))[:10],
            return_date=str(item.get("return_at", return_date))[:10],
            airline=str(item.get("airline", "")),
            airline_iata=str(item.get("airline", "")),
            price_usd=round(price_per_person * total_passengers, 2),
            duration_hours=round(duration_to / 60, 1) if duration_to else 0.0,
            stops=int(item.get("transfers", 0)),
            provider="travelpayouts",
        ))
    return results


def _serpapi_search(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        raise FlightSearchError("SERPAPI_API_KEY not set")

    children_count = len([a for a in config.children_ages if a >= 2])
    infants = len([a for a in config.children_ages if a < 2])

    params: dict = {
        "engine": "google_flights",
        "departure_id": config.home_airport,
        "arrival_id": destination,
        "outbound_date": departure_date,
        "return_date": return_date,
        "adults": config.adults,
        "currency": "USD",
        "hl": "en",
        "type": "1",
        "api_key": api_key,
    }
    if children_count:
        params["children"] = children_count
    if infants:
        params["infants_on_lap"] = infants
    if config.prefer_nonstop:
        params["stops"] = "1"  # SerpAPI: "1" = nonstop only

    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
        if resp.status_code == 401:
            raise FlightSearchError("SerpAPI key invalid")
        if not resp.ok:
            raise FlightSearchError(f"SerpAPI HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()

        price_insights = data.get("price_insights", {})
        price_level = price_insights.get("price_level", "")
        is_deal = price_level == "low"

        results = []
        for flight in data.get("best_flights", []) + data.get("other_flights", []):
            price = float(flight.get("price", 0))
            if price <= 0:
                continue
            segs = flight.get("flights", [])
            if not segs:
                continue
            total_mins = int(flight.get("total_duration", 0))
            airline = segs[0].get("airline", "")
            stops = len(flight.get("layovers", []))
            dep_time = segs[0].get("departure_airport", {}).get("time", "")
            dep_date = dep_time[:10] if dep_time else departure_date
            results.append(FlightOption(
                origin=config.home_airport,
                destination=destination,
                departure_date=dep_date,
                return_date=return_date,
                airline=airline,
                airline_iata="",
                price_usd=price,
                duration_hours=round(total_mins / 60, 1),
                stops=stops,
                provider="serpapi",
                is_deal=is_deal,
                price_level=price_level,
            ))
        return results
    except FlightSearchError:
        raise
    except Exception as e:
        raise FlightSearchError(f"SerpAPI unexpected: {e}") from e


def _kiwi_search(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    api_key = os.environ.get("KIWI_API_KEY", "")
    if not api_key:
        raise FlightSearchError("KIWI_API_KEY not set")

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
                "max_stopovers": 1,
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
        origin=origin, destination=destination,
        departure_date=departure_date, return_date=return_date,
        airline=airline_iata, airline_iata=airline_iata,
        price_usd=price, duration_hours=duration_hours,
        stops=stops, provider="amadeus",
    )


def _parse_kiwi_offer(item: dict, origin: str, destination: str) -> FlightOption:
    price = float(item.get("price", 0))
    duration_s = item.get("duration", {}).get("departure", 0)
    duration_hours = duration_s / 3600 if duration_s else 0.0
    routes = item.get("route", [])
    airline_iata = routes[0].get("airline", "") if routes else ""
    stops = max(0, len(routes) - 1)
    return FlightOption(
        origin=origin, destination=destination,
        departure_date=item.get("local_departure", "")[:10],
        return_date=item.get("local_arrival", "")[:10],
        airline=airline_iata, airline_iata=airline_iata,
        price_usd=price, duration_hours=round(duration_hours, 1),
        stops=stops, provider="kiwi",
    )


def _iso_duration_to_hours(duration: str) -> float:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration)
    if not match:
        return 0.0
    return round(int(match.group(1) or 0) + int(match.group(2) or 0) / 60, 2)


def _reformat_date(iso_date: str) -> str:
    parts = iso_date.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


class FlightSearchError(Exception):
    pass
