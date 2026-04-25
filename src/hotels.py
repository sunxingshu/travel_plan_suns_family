import logging
import math
import os
import requests
from typing import Optional

from src.models import FamilyConfig, HotelOption

logger = logging.getLogger(__name__)

_XOTELO_URL = "https://data.xotelo.com/api/rates"
_TIMEOUT = 15


def get_best_hotel(
    config: FamilyConfig,
    destination_iata: str,
    check_in: str,
    check_out: str,
    city_name: str = "",
) -> Optional[HotelOption]:
    """
    Returns the best HotelOption (cheapest above star_rating_min).
    SerpAPI is used when SERPAPI_API_KEY is set; falls back to Travelpayouts Hotellook.
    Returns None if no hotel is found.
    """
    nights = _count_nights(check_in, check_out)
    if nights <= 0:
        return None

    if os.environ.get("SERPAPI_API_KEY", "").strip() and city_name:
        try:
            return _serpapi_hotel_search(config, city_name, check_in, check_out, nights)
        except HotelSearchError as e:
            logger.warning("SerpAPI hotel search failed for %s: %s — falling back", city_name, e)

    try:
        return _travelpayouts_search(config, destination_iata, city_name, check_in, check_out, nights)
    except HotelSearchError as e:
        logger.warning("Hotel search failed for %s: %s", destination_iata, e)
        return None
    except Exception as e:
        logger.warning("Unexpected hotel error for %s: %s", destination_iata, e)
        return None


def _hotellook_city_id(iata: str, city_name: str, token: str) -> Optional[str]:
    """Resolve IATA or city name to Hotellook numeric city ID. Returns None if not found."""
    for query in filter(None, [city_name, iata]):
        try:
            resp = requests.get(
                "https://engine.hotellook.com/api/v2/lookup.json",
                params={"query": query, "lang": "en", "lookFor": "city", "limit": 1, "token": token},
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                logger.debug("Hotellook lookup HTTP %s for query=%r", resp.status_code, query)
                continue
            locations = resp.json().get("results", {}).get("locations", [])
            if locations:
                city_id = str(locations[0]["id"])
                logger.debug("Hotellook city ID for %s/%s → %s (%s)",
                             iata, city_name, city_id, locations[0].get("name", ""))
                return city_id
        except Exception as e:
            logger.debug("Hotellook lookup error for %r: %s", query, e)
    logger.warning("Hotellook city ID lookup failed for %s / %s", iata, city_name)
    return None


def _travelpayouts_search(
    config: FamilyConfig,
    city_iata: str,
    city_name: str,
    check_in: str,
    check_out: str,
    nights: int,
) -> Optional[HotelOption]:
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        raise HotelSearchError("TRAVELPAYOUTS_TOKEN not set")

    location = _hotellook_city_id(city_iata, city_name, token)
    if location is None:
        raise HotelSearchError(f"Hotellook city ID not found for {city_iata}/{city_name}")
    children_param = ",".join(str(a) for a in config.children_ages) if config.children_ages else ""

    try:
        params: dict = {
            "location": location,
            "checkIn": check_in,
            "checkOut": check_out,
            "adults": config.adults,
            "currency": "usd",
            "limit": 25,
            "token": token,
        }
        if children_param:
            params["children"] = children_param

        resp = requests.get(
            "https://engine.hotellook.com/api/v2/cache.json",
            params=params,
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            raise HotelSearchError(f"Travelpayouts HTTP {resp.status_code}: {resp.text[:200]}")

        hotels = resp.json()
        if not isinstance(hotels, list):
            hotels = hotels.get("hotels", []) if isinstance(hotels, dict) else []

        options = []
        for h in hotels:
            stars = float(h.get("stars", 0) or 0)
            if stars < config.hotel_star_min:
                continue
            price_per_night = float(h.get("priceFrom", 0) or 0)
            if price_per_night <= 0:
                continue
            options.append(HotelOption(
                name=h.get("hotelName", h.get("name", "Unknown")),
                brand="",
                star_rating=stars,
                price_per_night_usd=round(price_per_night, 2),
                total_price_usd=round(price_per_night * nights, 2),
                nights=nights,
                location=city_iata,
                provider="travelpayouts",
            ))
        return min(options, key=lambda h: h.total_price_usd) if options else None
    except HotelSearchError:
        raise
    except Exception as e:
        raise HotelSearchError(f"Travelpayouts unexpected: {e}") from e


def _serpapi_hotel_search(
    config: FamilyConfig,
    city_name: str,
    check_in: str,
    check_out: str,
    nights: int,
) -> Optional[HotelOption]:
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        raise HotelSearchError("SERPAPI_API_KEY not set")

    try:
        params: dict = {
            "engine": "google_hotels",
            "q": city_name,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "adults": config.adults,
            "currency": "USD",
            "gl": "us",
            "hl": "en",
            "api_key": api_key,
        }
        children_count = len(config.children_ages)
        if children_count > 0:
            params["children"] = children_count
            # SerpAPI requires children_ages when children > 0
            params["children_ages"] = ",".join(str(a) for a in config.children_ages)

        resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
        if resp.status_code == 401:
            raise HotelSearchError("SerpAPI key invalid or quota exhausted")
        if not resp.ok:
            raise HotelSearchError(f"SerpAPI Hotels HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        options = []
        for prop in data.get("properties", []):
            # Parse star rating from "hotel_class" like "4-star hotel"
            hotel_class = prop.get("hotel_class", "") or ""
            stars = 0.0
            if "star" in hotel_class.lower():
                try:
                    stars = float(hotel_class.split("-")[0].strip())
                except (ValueError, IndexError):
                    stars = 3.0
            else:
                # Use overall_rating as proxy if no hotel_class
                stars = float(prop.get("overall_rating", 0) or 0)

            if stars < config.hotel_star_min:
                continue

            # Parse price per night
            rate = prop.get("rate_per_night", {}) or {}
            price_str = rate.get("lowest", "") or rate.get("extracted_lowest", "") or "0"
            try:
                price_per_night = float(str(price_str).replace("$", "").replace(",", "").strip() or 0)
            except ValueError:
                price_per_night = 0.0

            if price_per_night <= 0:
                # Try prices array
                prices = prop.get("prices", [])
                if prices:
                    try:
                        price_per_night = float(prices[0].get("before_taxes_fees", 0) or 0)
                    except (ValueError, TypeError):
                        price_per_night = 0.0

            if price_per_night <= 0:
                continue

            options.append(HotelOption(
                name=prop.get("name", "Unknown Hotel"),
                brand="",
                star_rating=max(stars, 3.0),
                price_per_night_usd=round(price_per_night, 2),
                total_price_usd=round(price_per_night * nights, 2),
                nights=nights,
                location=city_name,
                provider="serpapi",
            ))

        return min(options, key=lambda h: h.total_price_usd) if options else None
    except HotelSearchError:
        raise
    except Exception as e:
        raise HotelSearchError(f"SerpAPI Hotels unexpected: {e}") from e


def _xotelo_search(
    config: FamilyConfig,
    city_iata: str,
    check_in: str,
    check_out: str,
    nights: int,
) -> Optional[HotelOption]:
    # Xotelo requires a hotel_key; without a specific hotel key it can't search by city.
    # As a free fallback with no city-search capability, we return None and log the limitation.
    logger.info(
        "Xotelo does not support city-level hotel search — skipping hotel data for %s", city_iata
    )
    return None


def _count_nights(check_in: str, check_out: str) -> int:
    from datetime import date
    return (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days


def _rooms_needed(adults: int, children_count: int) -> int:
    return max(1, math.ceil((adults + children_count) / 3))


class HotelSearchError(Exception):
    pass
