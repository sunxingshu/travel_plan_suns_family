from __future__ import annotations

import logging
import os
import re
import requests
from typing import Optional

from src.models import (
    FamilyConfig,
    FlightOption,
    DreamDestination,
)

logger = logging.getLogger(__name__)

# Kiwi/Tequila — optional Tier 2 provider. Requires KIWI_API_KEY.
_KIWI_URL = "https://api.tequila.kiwi.com/v2/search"

_TIMEOUT = 15

# Popular family-friendly destinations reachable from SFO within 10h.
# Used by broad search to price each route.
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

# Approximate one-way flight hours from SFO.
# Used when Travelpayouts doesn't return duration data (which is most of the time).
_APPROX_FLIGHT_HOURS: dict[str, float] = {
    # US domestic (short-haul)
    "LAX": 1.3, "SAN": 1.5, "LAS": 1.5, "SEA": 2.2, "PDX": 2.0,
    "PHX": 2.0, "DEN": 2.5, "SLC": 2.3, "SBA": 1.0, "SMF": 0.8,
    "BUR": 1.2, "ONT": 1.3, "OAK": 0.2, "SJC": 0.2,
    # US domestic (medium-haul)
    "ORD": 4.2, "DFW": 3.5, "IAH": 3.8, "ATL": 4.5, "MSP": 3.8,
    "DTW": 4.5, "CLT": 4.8, "AUS": 3.5, "SAT": 3.5, "MSY": 4.0,
    "BNA": 4.0, "STL": 4.0, "MCI": 3.5, "IND": 4.3,
    # US domestic (long-haul)
    "JFK": 5.5, "EWR": 5.5, "BOS": 5.5, "IAD": 5.3, "DCA": 5.3,
    "PHL": 5.3, "MIA": 5.5, "FLL": 5.5, "MCO": 5.3, "TPA": 5.2,
    "SJU": 7.0, "PIT": 5.0, "RDU": 5.0, "BWI": 5.3,
    # Hawaii
    "HNL": 5.5, "OGG": 5.3, "LIH": 5.5, "KOA": 5.2,
    # Mexico & Central America
    "CUN": 4.5, "PVR": 3.5, "SJD": 2.5, "MEX": 4.5, "GDL": 4.0,
    "SJO": 5.5, "LIR": 5.5, "PTY": 7.0, "BZE": 5.0,
    # Caribbean
    "MBJ": 6.5, "NAS": 6.5, "AUA": 7.5,
    # Canada
    "YVR": 2.5, "YYZ": 5.0, "YUL": 5.5, "YYC": 3.0, "YEG": 3.0,
    # Europe
    "LHR": 10.5, "CDG": 11.0, "AMS": 10.5, "FRA": 11.0,
    "FCO": 12.0, "BCN": 11.5, "MAD": 11.5, "LIS": 11.0,
    "DUB": 10.5, "CPH": 11.0, "OSL": 11.0, "ARN": 11.5,
    "MUC": 11.5, "ZRH": 11.5, "VIE": 12.0, "PRG": 12.0,
    "BUD": 12.5, "WAW": 12.0, "ATH": 13.0, "IST": 13.0,
    "HEL": 11.0, "KEF": 9.0, "EDI": 10.5,
    # Asia Pacific
    "NRT": 10.5, "HND": 10.5, "ICN": 11.5, "PVG": 12.0,
    "PEK": 11.5, "HKG": 13.0, "TPE": 12.0, "BKK": 14.5,
    "SIN": 16.0, "KUL": 16.5, "SGN": 14.5, "HAN": 14.0,
    "MNL": 14.0, "DEL": 16.0, "BOM": 17.0, "BLR": 17.5,
    "SYD": 14.5, "MEL": 15.0, "AKL": 13.0, "NAN": 11.0,
    # Middle East
    "DXB": 16.0, "DOH": 16.5, "AUH": 16.5, "TLV": 14.5,
    # South America
    "GRU": 12.5, "EZE": 12.0, "BOG": 7.5, "LIM": 9.0,
    "SCL": 12.0, "GIG": 12.5,
}


def get_estimated_duration(origin: str, destination: str) -> float:
    """Return estimated flight hours. Falls back to a generic estimate if unknown."""
    # Direct lookup (assumes origin is SFO-area)
    est = _APPROX_FLIGHT_HOURS.get(destination, 0.0)
    if est > 0:
        return est
    # Very rough fallback — better than 0.0
    return 6.0


def _validate_flight(f: FlightOption, config: FamilyConfig) -> bool:
    """Filter out bad flight data: same-date, zero duration, etc."""
    # Reject same-date flights (departure == return)
    if f.departure_date and f.return_date and f.departure_date == f.return_date:
        return False
    # Reject if return is before departure
    if f.departure_date and f.return_date and f.return_date < f.departure_date:
        return False
    # Reject impossibly short trips (less than min duration)
    if f.departure_date and f.return_date:
        from datetime import date
        try:
            dep = date.fromisoformat(f.departure_date)
            ret = date.fromisoformat(f.return_date)
            trip_days = (ret - dep).days
            if trip_days < config.trip_duration_min:
                return False
        except ValueError:
            pass
    return True


def get_best_flight(
    config: FamilyConfig,
    destination_iata: str,
    date_pairs: list[tuple[str, str]],
) -> Optional[FlightOption]:
    """
    Returns the best FlightOption from up to 3 date pairs.
    When prefer_nonstop=True: nonstop results are preferred; falls back to 1-stop if none found.
    Provider priority: SerpAPI → Amadeus → Travelpayouts → Kiwi.
    """
    candidates: list[FlightOption] = []

    for departure, return_date in date_pairs[:3]:
        for search_fn in _get_search_functions():
            try:
                options = search_fn(config, destination_iata, departure, return_date)
                valid = [f for f in options if f.duration_hours <= config.max_flight_hours]
                candidates.extend(valid)
                if valid:
                    break  # Got results from this provider, skip lower-priority ones
            except FlightSearchError as e:
                logger.warning(
                    "Flight search failed (%s→%s, %s, %s): %s",
                    config.home_airport, destination_iata, departure,
                    search_fn.__name__, e,
                )

    # Fallback: cheap prices endpoint has much broader coverage than prices_for_dates
    if not candidates and os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
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


def _get_search_functions() -> list:
    """Return ordered list of search functions based on available API keys."""
    fns = []
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        fns.append(_serpapi_search)
    # Check Amadeus availability
    try:
        from src.amadeus_auth import is_configured
        if is_configured():
            fns.append(_amadeus_search)
    except ImportError:
        pass
    if os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
        fns.append(_travelpayouts_search)
    if os.environ.get("KIWI_API_KEY", "").strip():
        fns.append(_kiwi_search)
    # If nothing is configured, try Travelpayouts anyway (it will raise a clear error)
    if not fns:
        fns.append(_travelpayouts_search)
    return fns


def get_all_cheap_flights(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Broad search: prices each popular destination across candidate windows.
    Returns the cheapest flight per destination across all windows, sorted by price.
    Cascades through available providers: SerpAPI → Amadeus → Travelpayouts.
    """
    results: list[FlightOption] = []

    # Tier 1: SerpAPI (best data — scrapes Google Flights)
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        results = _serpapi_broad_search(config, candidate_windows)
        logger.info("SerpAPI broad search: %d destinations priced", len(results))
        if len(results) >= 3:
            return results

    # Tier 2: Amadeus (real GDS data, free tier)
    try:
        from src.amadeus_auth import is_configured
        if is_configured():
            amadeus_results = _amadeus_broad_search(config, candidate_windows)
            logger.info("Amadeus broad search: %d destinations priced", len(amadeus_results))
            # Merge: keep cheapest per destination
            results = _merge_flight_results(results, amadeus_results)
            if len(results) >= 3:
                return results
    except ImportError:
        pass

    # Tier 3: Travelpayouts (cached data — broader coverage but less reliable for US)
    if os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
        tp_results = _travelpayouts_broad_search(config, candidate_windows)
        logger.info("Travelpayouts broad search: %d destinations priced", len(tp_results))
        results = _merge_flight_results(results, tp_results)

    if not results:
        logger.warning("No broad flight search providers returned results")

    # Post-process: fill in estimated durations and filter bad data
    for f in results:
        if f.duration_hours <= 0.0:
            f.duration_hours = get_estimated_duration(f.origin, f.destination)
    results = [f for f in results if _validate_flight(f, config)]

    return results


def get_dream_flights(config: FamilyConfig) -> list[FlightOption]:
    """Retrieve exactly 1 cheapest flight per dream destination based on their requested trip duration."""
    results = []
    total_passengers = config.adults + len(config.children_ages)

    for dream in config.dream_destinations:
        from src.config_loader import _SCHOOL_HOLIDAY_WINDOWS, _date_fits_pattern
        from datetime import date, timedelta, datetime
        from dateutil.relativedelta import relativedelta
        today = date.today()
        earliest = today + timedelta(weeks=config.availability.book_at_least_weeks_ahead)
        latest = today + relativedelta(months=config.availability.lookahead_months)
        current = earliest
        
        # Build up to 2 specific candidate windows for this dream duration to keep api costs trivial
        date_pairs = []
        while current <= latest and len(date_pairs) < 2:
            if _date_fits_pattern(current, config.availability.pattern):
                dep_str = current.isoformat()
                ret_str = (current + timedelta(days=dream.duration_days)).isoformat()
                
                # Check excluded dates
                days_in_trip = [(current + timedelta(days=i)).isoformat() for i in range(dream.duration_days + 1)]
                if not any(d in config.availability.exclude_dates for d in days_in_trip):
                    date_pairs.append((dep_str, ret_str))
            current += timedelta(days=7)

        # Call get_best_flight which natively cascades through SerpAPI (real-time) -> Amadeus -> TP
        best_flight = get_best_flight(config, dream.iata, date_pairs)

        # Fallback to Travelpayouts Calendar API (broad cache, scans all existing durations)
        if not best_flight and os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
            try:
                resp = requests.get(
                    "https://api.travelpayouts.com/v1/prices/calendar",
                    params={
                        "origin": config.home_airport,
                        "destination": dream.iata,
                        "currency": "usd",
                        "token": os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
                    },
                    timeout=15
                )
                if resp.ok and resp.json().get("success"):
                    cal_data = resp.json().get("data", {})
                    valid_flights = []
                    for f_id, item in cal_data.items():
                        dep = item.get("departure_at")
                        ret = item.get("return_at")
                        prc = float(item.get("price", 0))
                        if dep and ret and prc > 0:
                            try:
                                d_dt = datetime.fromisoformat(dep.replace("Z", "+00:00"))
                                r_dt = datetime.fromisoformat(ret.replace("Z", "+00:00"))
                                days = (r_dt - d_dt).days
                                valid_flights.append((abs(days - dream.duration_days), prc, item))
                            except: pass
                    
                    if valid_flights:
                        # Sort by how close they are to target duration, then by price
                        valid_flights.sort(key=lambda x: (x[0], x[1]))
                        best_item = valid_flights[0][2]
                        best_flight = FlightOption(
                            origin=config.home_airport,
                            destination=dream.iata,
                            departure_date=best_item.get("departure_at")[:10],
                            return_date=best_item.get("return_at")[:10],
                            airline=str(best_item.get("airline", "")),
                            airline_iata=str(best_item.get("airline", "")),
                            price_usd=round(float(best_item.get("price")) * total_passengers, 2),
                            duration_hours=0.0,
                            stops=int(best_item.get("transfers", 0)),
                            provider="travelpayouts"
                        )
            except Exception as e:
                logger.warning("Calendar fallback failed for %s: %s", dream.iata, e)

        if best_flight:
            if best_flight.duration_hours <= 0.0:
                best_flight.duration_hours = get_estimated_duration(best_flight.origin, best_flight.destination)
            results.append(best_flight)

    return results


def _merge_flight_results(
    existing: list[FlightOption],
    new: list[FlightOption],
) -> list[FlightOption]:
    """Merge two flight lists, keeping cheapest per destination."""
    best: dict[str, FlightOption] = {}
    for f in existing + new:
        cur = best.get(f.destination)
        if cur is None or f.price_usd < cur.price_usd:
            best[f.destination] = f
    return sorted(best.values(), key=lambda f: f.price_usd)


# ─────────────────────────────────────────────────────────────────────────────
# Amadeus Self-Service
# ─────────────────────────────────────────────────────────────────────────────

def _amadeus_search(
    config: FamilyConfig,
    destination: str,
    departure_date: str,
    return_date: str,
) -> list[FlightOption]:
    """Search Amadeus Flight Offers for a specific route + dates."""
    from src.amadeus_auth import get_access_token, get_base_url, AmadeusAuthError

    try:
        token = get_access_token()
    except AmadeusAuthError as e:
        raise FlightSearchError(str(e)) from e

    base_url = get_base_url()
    children_count = len([a for a in config.children_ages if a >= 2])
    infants = len([a for a in config.children_ages if a < 2])

    # Build traveler list for Amadeus
    travelers = []
    for i in range(config.adults):
        travelers.append({"id": str(i + 1), "travelerType": "ADULT"})
    offset = config.adults
    for i in range(children_count):
        travelers.append({"id": str(offset + i + 1), "travelerType": "CHILD"})
    offset += children_count
    for i in range(infants):
        travelers.append({
            "id": str(offset + i + 1),
            "travelerType": "SEATED_INFANT",
            "associatedAdultId": str(i + 1),
        })

    body = {
        "currencyCode": "USD",
        "originDestinations": [
            {
                "id": "1",
                "originLocationCode": config.home_airport,
                "destinationLocationCode": destination,
                "departureDateTimeRange": {"date": departure_date},
            },
            {
                "id": "2",
                "originLocationCode": destination,
                "destinationLocationCode": config.home_airport,
                "departureDateTimeRange": {"date": return_date},
            },
        ],
        "travelers": travelers,
        "sources": ["GDS"],
        "searchCriteria": {
            "maxFlightOffers": 10,
            "flightFilters": {
                "cabinRestrictions": [
                    {
                        "cabin": "ECONOMY",
                        "coverage": "MOST_SEGMENTS",
                        "originDestinationIds": ["1", "2"],
                    }
                ],
            },
        },
    }

    if config.prefer_nonstop:
        body["searchCriteria"]["flightFilters"]["connectionRestriction"] = {
            "maxNumberOfConnections": 0
        }

    try:
        resp = requests.post(
            f"{base_url}/v2/shopping/flight-offers",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )
        if resp.status_code == 401:
            raise FlightSearchError("Amadeus token expired or invalid")
        if not resp.ok:
            # If nonstop-only returned nothing, retry with connections
            if config.prefer_nonstop and resp.status_code == 400:
                del body["searchCriteria"]["flightFilters"]["connectionRestriction"]
                resp = requests.post(
                    f"{base_url}/v2/shopping/flight-offers",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=20,
                )
                if not resp.ok:
                    raise FlightSearchError(f"Amadeus HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                raise FlightSearchError(f"Amadeus HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        results = []
        for offer in data.get("data", []):
            price = float(offer.get("price", {}).get("grandTotal", 0))
            if price <= 0:
                continue

            # Parse outbound itinerary (first segment group)
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                continue

            outbound = itineraries[0]
            segments = outbound.get("segments", [])
            if not segments:
                continue

            duration_iso = outbound.get("duration", "")
            duration_hours = _iso_duration_to_hours(duration_iso)
            airline = segments[0].get("carrierCode", "")
            stops = max(0, len(segments) - 1)
            dep_date = segments[0].get("departure", {}).get("at", departure_date)[:10]

            # Get return date from last itinerary
            ret_date = return_date
            if len(itineraries) > 1:
                ret_segments = itineraries[1].get("segments", [])
                if ret_segments:
                    ret_date = ret_segments[-1].get("arrival", {}).get("at", return_date)[:10]

            results.append(FlightOption(
                origin=config.home_airport,
                destination=destination,
                departure_date=dep_date,
                return_date=ret_date,
                airline=airline,
                airline_iata=airline,
                price_usd=price,
                duration_hours=duration_hours,
                stops=stops,
                provider="amadeus",
            ))

        # If nonstop search returned nothing, retry with stops
        if not results and config.prefer_nonstop:
            if "connectionRestriction" in body.get("searchCriteria", {}).get("flightFilters", {}):
                del body["searchCriteria"]["flightFilters"]["connectionRestriction"]
                return _amadeus_search.__wrapped__(config, destination, departure_date, return_date) if hasattr(_amadeus_search, '__wrapped__') else []

        return results

    except FlightSearchError:
        raise
    except Exception as e:
        raise FlightSearchError(f"Amadeus unexpected: {e}") from e


def _amadeus_broad_search(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Search Amadeus for each popular destination with one representative window per month.
    """
    from datetime import date, timedelta
    from src.amadeus_auth import get_access_token, get_base_url, AmadeusAuthError

    try:
        token = get_access_token()
    except AmadeusAuthError as e:
        logger.warning("Amadeus auth failed for broad search: %s", e)
        return []

    min_date = (date.today() + timedelta(weeks=2)).isoformat()
    valid_windows = [(dep, ret) for dep, ret in candidate_windows if dep >= min_date]

    # One representative window per calendar month
    windows_by_month: dict[str, tuple[str, str]] = {}
    for dep, ret in valid_windows:
        month_key = dep[:7]
        if month_key not in windows_by_month:
            windows_by_month[month_key] = (dep, ret)
    search_windows = list(windows_by_month.values())

    if not search_windows:
        return []

    best_by_dest: dict[str, FlightOption] = {}

    for iata, _city in _POPULAR_FROM_SFO:
        # Use first available window for each destination to limit API calls
        dep, ret = search_windows[0]
        try:
            results = _amadeus_search(config, iata, dep, ret)
            if results:
                best = min(results, key=lambda f: f.price_usd)
                existing = best_by_dest.get(iata)
                if existing is None or best.price_usd < existing.price_usd:
                    best_by_dest[iata] = best
        except FlightSearchError as e:
            logger.debug("Amadeus broad search failed for %s: %s", iata, e)

    return list(best_by_dest.values())


# ─────────────────────────────────────────────────────────────────────────────
# Travelpayouts (cached data from Aviasales)
# ─────────────────────────────────────────────────────────────────────────────

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
                "market": "us",
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
        params={
            "origin": config.home_airport,
            "destination": destination,
            "currency": "usd",
            "market": "us",
            "token": token,
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        return []
    data = resp.json()
    if not data.get("success"):
        return []
    data_dict = data.get("data", {})
    item = data_dict.get(destination)
    if not item and data_dict:
        # TP often returns city macro-code (e.g. TYO) instead of airport (NRT)
        item = list(data_dict.values())[0]
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
            "market": "us",
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


def _travelpayouts_broad_search(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
) -> list[FlightOption]:
    """
    Broad search using Travelpayouts v1/prices/cheap endpoint.
    This returns cheapest cached prices to ALL destinations from an origin,
    which is much broader than prices_for_dates (route-specific).
    """
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        return []

    total_passengers = config.adults + len(config.children_ages)
    best_by_dest: dict[str, FlightOption] = {}

    # v1/prices/cheap returns all routes from origin — no need to loop destinations
    try:
        resp = requests.get(
            "https://api.travelpayouts.com/v1/prices/cheap",
            headers={"X-Access-Token": token},
            params={
                "origin": config.home_airport,
                "destination": "-",
                "currency": "usd",
            },
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            logger.debug("Travelpayouts v1/prices/cheap HTTP %s", resp.status_code)
            return []

        data = resp.json().get("data", {})
        for dest_iata, routes in data.items():
            if not isinstance(routes, dict):
                continue
            # Routes can be {0: {...}, 1: {...}} keyed by number of stops
            for _stops_key, item in routes.items():
                if not isinstance(item, dict):
                    continue
                price_per_person = float(item.get("price", 0))
                if price_per_person <= 0:
                    continue
                total_price = round(price_per_person * total_passengers, 2)
                dep_date = str(item.get("departure_at", ""))[:10]
                ret_date = str(item.get("return_at", ""))[:10]
                stops = int(item.get("number_of_changes", 0))

                flight = FlightOption(
                    origin=config.home_airport,
                    destination=dest_iata,
                    departure_date=dep_date,
                    return_date=ret_date,
                    airline=str(item.get("airline", "")),
                    airline_iata=str(item.get("airline", "")),
                    price_usd=total_price,
                    duration_hours=0.0,  # v1 endpoint doesn't provide duration
                    stops=stops,
                    provider="travelpayouts",
                )
                existing = best_by_dest.get(dest_iata)
                if existing is None or flight.price_usd < existing.price_usd:
                    best_by_dest[dest_iata] = flight
    except Exception as e:
        logger.debug("Travelpayouts broad search failed: %s", e)

    # Also try v3/get_cheap_prices per-month for better data
    from datetime import date, timedelta
    min_date = (date.today() + timedelta(weeks=2)).isoformat()
    valid_windows = [(dep, ret) for dep, ret in candidate_windows if dep >= min_date]
    months_seen: set[str] = set()

    for dep, _ret in valid_windows:
        month_key = dep[:7]
        if month_key in months_seen:
            continue
        months_seen.add(month_key)

        try:
            resp = requests.get(
                "https://api.travelpayouts.com/aviasales/v3/get_cheap_prices",
                params={
                    "origin": config.home_airport,
                    "departure_at": month_key,
                    "unique": "false",
                    "sorting": "price",
                    "direct": "false",
                    "currency": "usd",
                    "market": "us",
                    "limit": 30,
                    "token": token,
                },
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                continue
            rdata = resp.json()
            if not rdata.get("success"):
                continue
            items = rdata.get("data", {})
            for dest_iata, item in items.items():
                if not isinstance(item, dict):
                    continue
                price_per_person = float(item.get("price", 0))
                if price_per_person <= 0:
                    continue
                total_price = round(price_per_person * total_passengers, 2)
                duration_to = int(item.get("duration_to", 0) or 0)
                flight = FlightOption(
                    origin=config.home_airport,
                    destination=dest_iata,
                    departure_date=str(item.get("departure_at", ""))[:10],
                    return_date=str(item.get("return_at", ""))[:10],
                    airline=str(item.get("airline", "")),
                    airline_iata=str(item.get("airline", "")),
                    price_usd=total_price,
                    duration_hours=round(duration_to / 60, 1) if duration_to else 0.0,
                    stops=int(item.get("number_of_changes", item.get("transfers", 0))),
                    provider="travelpayouts",
                )
                existing = best_by_dest.get(dest_iata)
                if existing is None or flight.price_usd < existing.price_usd:
                    best_by_dest[dest_iata] = flight
        except Exception as e:
            logger.debug("Travelpayouts v3/get_cheap_prices failed for %s: %s", month_key, e)

    return sorted(best_by_dest.values(), key=lambda f: f.price_usd)


# ─────────────────────────────────────────────────────────────────────────────
# SerpAPI (Google Flights scraping)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Kiwi / Tequila
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

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
