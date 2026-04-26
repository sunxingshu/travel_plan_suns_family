from __future__ import annotations

import logging
import requests
from datetime import date

from src.models import WeatherSummary

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_TIMEOUT = 10  # seconds


def get_weather_summary(
    api_key: str,
    destination_city: str,
    country_code: str,
    start_date: str,
    end_date: str,
) -> WeatherSummary | None:
    try:
        raw = _fetch_forecast(api_key, destination_city, country_code)
        return _parse_forecast(raw, destination_city, start_date, end_date)
    except WeatherAPIError as e:
        logger.warning("Weather fetch failed for %s: %s", destination_city, e)
        return None
    except Exception as e:
        logger.warning("Unexpected weather error for %s: %s", destination_city, e)
        return None


_COUNTRY_NORMALIZE = {
    "usa": "US", "united states": "US", "united states of america": "US",
    "uk": "GB", "united kingdom": "GB", "england": "GB",
    "canada": "CA", "mexico": "MX", "japan": "JP", "australia": "AU",
    "france": "FR", "germany": "DE", "italy": "IT", "spain": "ES",
    "thailand": "TH", "singapore": "SG", "indonesia": "ID",
}


def _normalize_country(raw: str) -> str:
    """Convert AI-generated country strings to 2-letter ISO codes."""
    cleaned = raw.strip()
    # Strip parenthetical qualifiers like "USA (Hawaii)" → "USA"
    if "(" in cleaned:
        cleaned = cleaned[:cleaned.index("(")].strip()
    lower = cleaned.lower()
    if lower in _COUNTRY_NORMALIZE:
        return _COUNTRY_NORMALIZE[lower]
    # Already a 2-letter code
    if len(cleaned) == 2:
        return cleaned.upper()
    return cleaned


def _fetch_forecast(api_key: str, city: str, country_code: str) -> dict:
    country = _normalize_country(country_code)
    # Try with country code first, fall back to city name only
    for q in [f"{city},{country}", city]:
        resp = requests.get(
            _FORECAST_URL,
            params={"q": q, "appid": api_key, "units": "metric", "cnt": 40},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401:
            raise WeatherAPIError("Invalid OpenWeatherMap API key")
        if resp.status_code == 404:
            continue
        if not resp.ok:
            raise WeatherAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    raise WeatherAPIError(f"City not found: {city}, {country_code}")


def _parse_forecast(
    raw: dict, destination: str, start_date: str, end_date: str
) -> WeatherSummary:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    today = date.today()

    # Check if the travel window is within the 5-day forecast range
    is_forecast = (start - today).days <= 5

    # Filter forecast entries that fall within our travel window
    entries = [
        item
        for item in raw.get("list", [])
        if start <= date.fromisoformat(item["dt_txt"][:10]) <= end
    ]

    if entries:
        avg_temp = sum(e["main"]["temp"] for e in entries) / len(entries)
        avg_precip = sum(e.get("pop", 0) for e in entries) / len(entries) * 100
        # Most common weather condition
        condition_counts: dict[str, int] = {}
        for e in entries:
            cond = e["weather"][0]["main"] if e.get("weather") else "Unknown"
            condition_counts[cond] = condition_counts.get(cond, 0) + 1
        conditions = max(condition_counts, key=condition_counts.get)  # type: ignore[arg-type]
    else:
        # Trip is beyond 5-day forecast window — use all available data as a seasonal proxy
        all_entries = raw.get("list", [])
        if all_entries:
            avg_temp = sum(e["main"]["temp"] for e in all_entries) / len(all_entries)
            avg_precip = sum(e.get("pop", 0) for e in all_entries) / len(all_entries) * 100
        else:
            avg_temp = 20.0
            avg_precip = 30.0
        conditions = "Forecast unavailable — typical seasonal estimate"
        is_forecast = False

    return WeatherSummary(
        destination=destination,
        travel_date_range=f"{start_date} to {end_date}",
        avg_temp_celsius=round(avg_temp, 1),
        conditions=conditions,
        precipitation_chance_pct=round(avg_precip, 1),
        is_forecast=is_forecast,
    )


class WeatherAPIError(Exception):
    pass
