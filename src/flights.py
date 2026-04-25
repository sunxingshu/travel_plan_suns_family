import logging
import os
import re
import requests
from typing import Optional

from src.models import FamilyConfig, FlightOption

logger = logging.getLogger(__name__)

# Kiwi/Tequila — optional Tier 2 provider. Requires KIWI_API_KEY.
_KIWI_URL = "https://api.tequila.kiwi.com/v2/search"

_TIMEOUT = 15

# Popular family-friendly destinations reachable from SFO within 10h.
# Used by the SerpAPI broad search to price each route.
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
    ("SJO", "San Jose, Costa Rica"),
    ("SJU", "San Juan"),
    ("JFK", "New York City"),
    ("SEA", "Seattle"),
    ("DEN", "Denver"),
    ("LHR", "London"),
    ("CDG", "Paris"),
    ("NRT", "Tokyo"),
    ("ICN", "Seoul"),
    ("SYD", "Sydney"),
    ("MEX", "Mexico City"),
    ("GRU", "São Paulo"),
    ("YYZ", "Toronto"),
    ("DXB", "Dubai"),
    ("BKK", "Bangkok"),
    ("SIN", "Singapore"),
]


def get_best_flight(
    config: FamilyConfig,
    destination_iata: str,
    date_pairs: list[tuple[str, str]],
) -> Optional[FlightOption]:
    """
    Returns the best FlightOption from up to 3 date pairs.
    When prefer_nonstop=True: nonstop results are preferred; falls back to 1-stop if none found.
    Provider: SerpAPI when SERPAPI_API_KEY is set (default), else Kiwi, else Travelpayouts.
    """
    provider = os.environ.get("FLIGHT_PROVIDER", "travelpayouts").lower()
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        provider = "serpapi"
    candidates: list[FlightOption] = []

    for departure, return_date in date_pairs[:3]:
        try:
            if provider == "serpapi":
                options = _serpapi_search(config, destination_iata, departure, return_date)
            elif provider == "kiwi":
                options = _kiwi_search(config, destination_iata, departure, return_date)
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


def get_all_cheap_flights(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Broad SerpAPI search: prices each popular destination across one window per calendar month.
    Returns the cheapest flight per destination across all windows, sorted by price.
    """
    if not os.environ.get("SERPAPI_API_KEY", "").strip():
        logger.warning("SERPAPI_API_KEY not set — broad flight search skipped")
        return []
    results = _serpapi_broad_search(config, candidate_windows)
    logger.info("SerpAPI broad search: %d destinations priced", len(results))
    return results


def _serpapi_broad_search(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Run SerpAPI Google Flights for each popular destination across up to one window per month.
    Keeps the cheapest flight per destination so each route gets its own optimal date.
    """
    from datetime import date, timedelta
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return []

    min_date = (date.today() + timedelta(weeks=2)).isoformat()
    valid_windows = [(dep, ret) for dep, ret in candidate_windows if dep >= min_date]

    # One representative window per calendar month — covers the full 6-month lookahead.
    windows_by_month: dict[str, tuple[str, str]] = {}
    for dep, ret in valid_windows:
        month_key = dep[:7]  # "YYYY-MM"
        if month_key not in windows_by_month:
            windows_by_month[month_key] = (dep, ret)
    search_windows = list(windows_by_month.values())

    if not search_windows:
        return []

    children_count = len([a for a in config.children_ages if a >= 2])
    infants = len([a for a in config.children_ages if a < 2])

    best_by_dest: dict[str, FlightOption] = {}

    for iata, _city in _POPULAR_FROM_SFO:
        for departure_date, return_date in search_windows:
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
                    logger.debug("SerpAPI broad HTTP %s for %s on %s", resp.status_code, iata, departure_date)
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

                flight = FlightOption(
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
                )
                existing = best_by_dest.get(iata)
                if existing is None or flight.price_usd < existing.price_usd:
                    best_by_dest[iata] = flight

            except Exception as e:
                logger.debug("SerpAPI broad search failed for %s on %s: %s", iata, departure_date, e)

    return list(best_by_dest.values())


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
    _TP_FLIGHT_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

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
    """Fallback when prices_for_dates has no cached data for specific dates."""
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
    _TP_FLIGHT_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
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
