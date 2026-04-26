from __future__ import annotations

import logging
import math
import os
import requests
from typing import Optional

from src.models import FamilyConfig, HotelOption

logger = logging.getLogger(__name__)

_TIMEOUT = 15

# Average 3-star hotel prices per night (USD) for popular destinations.
# Sources: Kayak/Booking.com averages, April 2026. Used as fallback when no API is available.
_HOTEL_ESTIMATES: dict[str, float] = {
    # US domestic
    "HNL": 220, "OGG": 280, "LAS": 100, "MCO": 140, "MIA": 180,
    "SAN": 170, "LAX": 180, "JFK": 200, "SEA": 170, "DEN": 150,
    "SFO": 200, "SBA": 200, "PHX": 130, "MSP": 140, "AUS": 160,
    "BOS": 190, "ORD": 160, "ATL": 140, "DCA": 180, "SJU": 150,
    # Mexico & Caribbean
    "CUN": 120, "PVR": 110, "SJD": 150, "MEX": 90, "GDL": 80,
    # Canada
    "YVR": 170, "YYZ": 150, "YUL": 140,
    # Central America
    "SJO": 100, "LIR": 120, "PTY": 110, "BZE": 130,
    # Europe
    "LHR": 200, "CDG": 180, "FCO": 150, "BCN": 140, "AMS": 170,
    "LIS": 120, "PRG": 100, "BUD": 80, "DUB": 160,
    # Asia
    "NRT": 130, "ICN": 110, "BKK": 50, "SIN": 160, "HKG": 140,
    "TPE": 90, "MNL": 60, "KUL": 60, "SGN": 40,
    # Oceania
    "SYD": 170, "AKL": 150,
    # Middle East
    "DXB": 150, "DOH": 140,
    # South America
    "GRU": 80, "BOG": 70, "LIM": 70, "SCL": 90, "EZE": 80,
}
_DEFAULT_ESTIMATE = 140  # Global average for a 3-star hotel

_RESORT_DESTINATIONS = {
    # Hawaii
    "HNL", "OGG", "LIH", "KOA",
    # Mexico/Caribbean
    "CUN", "PVR", "SJD", "MBJ", "NAS", "AUA", "SJO", "LIR", "PTY", "BZE", "PUJ", "SJU",
    # Typical beach/resort international
    "NAN", "DXB", "DPS", "MLE", "HKT", "CEB", "PPT", "BOB",
    # US beach
    "MIA", "FLL", "TPA", "RSW", "EYW",
}


def get_best_hotel(
    config: FamilyConfig,
    destination_iata: str,
    check_in: str,
    check_out: str,
    city_name: str = "",
) -> Optional[HotelOption]:
    """
    Returns the best HotelOption (cheapest above target_stars).
    Provider priority: SerpAPI Google Hotels → Hotel estimate fallback.
    Returns None if no hotel is found.
    """
    nights = _count_nights(check_in, check_out)
    if nights <= 0:
        return None

    # Determine target star rating
    target_stars = 4.0 if destination_iata in _RESORT_DESTINATIONS else 3.0
    target_stars = max(target_stars, float(config.hotel_star_min))

    # Tier 1: SerpAPI Google Hotels (real data)
    if os.environ.get("SERPAPI_API_KEY", "").strip() and city_name:
        try:
            result = _serpapi_hotel_search(config, city_name, check_in, check_out, nights, target_stars)
            if result:
                return result
        except HotelSearchError as e:
            logger.warning("SerpAPI hotel search failed for %s: %s — falling back", city_name, e)

    # Tier 2: Estimate based on known city averages
    return _estimate_hotel(config, destination_iata, city_name, check_in, check_out, nights, target_stars)


def _estimate_hotel(
    config: FamilyConfig,
    destination_iata: str,
    city_name: str,
    check_in: str,
    check_out: str,
    nights: int,
    target_stars: float,
) -> Optional[HotelOption]:
    """
    Provide a hotel cost estimate based on known average prices for the destination.
    This is used when no hotel API is available (Hotellook was shut down Oct 2025).
    """
    price_per_night = _HOTEL_ESTIMATES.get(destination_iata, _DEFAULT_ESTIMATE)

    # Adjust for star rating preference
    if target_stars >= 4:
        price_per_night = round(price_per_night * 1.6, 2)
    elif target_stars >= 5:
        price_per_night = round(price_per_night * 2.5, 2)

    # Adjust for rooms needed (family with kids may need 2 rooms)
    rooms = _rooms_needed(config.adults, len(config.children_ages))
    total_per_night = round(price_per_night * rooms, 2)

    location = city_name if city_name else destination_iata
    return HotelOption(
        name=f"Avg {target_stars}★ hotel in {location}",
        brand="",
        star_rating=target_stars,
        price_per_night_usd=total_per_night,
        total_price_usd=round(total_per_night * nights, 2),
        nights=nights,
        location=location,
        provider="estimate",
    )


def _serpapi_hotel_search(
    config: FamilyConfig,
    city_name: str,
    check_in: str,
    check_out: str,
    nights: int,
    target_stars: float,
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

            if stars < target_stars:
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


def _count_nights(check_in: str, check_out: str) -> int:
    from datetime import date
    return (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days


def _rooms_needed(adults: int, children_count: int) -> int:
    return max(1, math.ceil((adults + children_count) / 3))


class HotelSearchError(Exception):
    pass
