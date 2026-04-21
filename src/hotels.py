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
    amadeus_client=None,
) -> Optional[HotelOption]:
    """
    Returns the best HotelOption (cheapest above star_rating_min).
    Provider selected by HOTEL_PROVIDER env var (default: travelpayouts).
    NOTE: Amadeus self-service is decommissioned July 17, 2026.
    Returns None if no hotel is found.
    """
    provider = os.environ.get("HOTEL_PROVIDER", "travelpayouts").lower()
    nights = _count_nights(check_in, check_out)
    if nights <= 0:
        return None

    try:
        if provider == "travelpayouts":
            return _travelpayouts_search(config, destination_iata, check_in, check_out, nights)
        elif provider == "xotelo":
            return _xotelo_search(config, destination_iata, check_in, check_out, nights)
        else:
            if amadeus_client is None:
                logger.warning("Amadeus client not provided for hotel search; trying Xotelo fallback")
                return _xotelo_search(config, destination_iata, check_in, check_out, nights)
            return _amadeus_search(amadeus_client, config, destination_iata, check_in, check_out, nights)
    except HotelSearchError as e:
        logger.warning("Hotel search failed for %s: %s", destination_iata, e)
        return None
    except Exception as e:
        logger.warning("Unexpected hotel error for %s: %s", destination_iata, e)
        return None


def _amadeus_search(
    amadeus_client,
    config: FamilyConfig,
    city_iata: str,
    check_in: str,
    check_out: str,
    nights: int,
) -> Optional[HotelOption]:
    from amadeus import ResponseError
    try:
        # Step 1: get hotel IDs in city
        ratings = list(range(max(1, config.hotel_star_min), 6))
        hotel_resp = amadeus_client.reference_data.locations.hotels.by_city.get(
            cityCode=city_iata,
            ratings=ratings,
        )
        hotel_ids = [h["hotelId"] for h in hotel_resp.data[:20]]
        if not hotel_ids:
            return None

        # Step 2: get offers for those hotels
        rooms = _rooms_needed(config.adults, len(config.children_ages))
        offers_resp = amadeus_client.shopping.hotel_offers_search.get(
            hotelIds=hotel_ids,
            checkInDate=check_in,
            checkOutDate=check_out,
            adults=config.adults,
            roomQuantity=rooms,
            currency="USD",
            bestRateOnly=True,
        )

        results: list[HotelOption] = []
        for item in offers_resp.data:
            hotel_info = item.get("hotel", {})
            offers = item.get("offers", [])
            if not offers:
                continue
            offer = offers[0]
            price_total = float(offer.get("price", {}).get("total", 0))
            if price_total <= 0:
                continue
            rating = float(hotel_info.get("rating", 0) or 0)
            if rating < config.hotel_star_min:
                continue
            results.append(HotelOption(
                name=hotel_info.get("name", "Unknown Hotel"),
                brand=hotel_info.get("brandCode", ""),
                star_rating=rating,
                price_per_night_usd=round(price_total / nights, 2),
                total_price_usd=price_total,
                nights=nights,
                location=hotel_info.get("cityCode", city_iata),
                provider="amadeus",
            ))

        return min(results, key=lambda h: h.total_price_usd) if results else None
    except ResponseError as e:
        raise HotelSearchError(f"Amadeus error: {e}") from e


def _travelpayouts_search(
    config: FamilyConfig,
    city_iata: str,
    check_in: str,
    check_out: str,
    nights: int,
) -> Optional[HotelOption]:
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if not token:
        raise HotelSearchError("TRAVELPAYOUTS_TOKEN not set")

    try:
        resp = requests.get(
            "https://yasen.hotellook.com/hotels/search",
            params={
                "cityId": city_iata,
                "checkIn": check_in,
                "checkOut": check_out,
                "adults": config.adults,
                "children": len(config.children_ages),
                "currency": "USD",
                "limit": 20,
                "token": token,
            },
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            raise HotelSearchError(f"Travelpayouts HTTP {resp.status_code}")
        hotels = resp.json().get("results", {}).get("hotels", [])
        options = []
        for h in hotels:
            stars = float(h.get("stars", 0) or 0)
            if stars < config.hotel_star_min:
                continue
            price = float(h.get("priceFrom", 0) or 0) * nights
            if price <= 0:
                continue
            options.append(HotelOption(
                name=h.get("name", "Unknown"),
                brand="",
                star_rating=stars,
                price_per_night_usd=round(price / nights, 2),
                total_price_usd=price,
                nights=nights,
                location=city_iata,
                provider="travelpayouts",
            ))
        return min(options, key=lambda h: h.total_price_usd) if options else None
    except HotelSearchError:
        raise
    except Exception as e:
        raise HotelSearchError(f"Travelpayouts unexpected: {e}") from e


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
