import json
import logging
import os
import re
import time
from openai import OpenAI

from src.models import DestinationPlan, FamilyConfig, TravelPlan

logger = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "deepseek/deepseek-r1:free"
_MAX_RETRIES = 2
_RETRY_DELAY = 3.0


def _get_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set.")
    return OpenAI(
        api_key=api_key,
        base_url=_OPENROUTER_BASE,
        default_headers={"HTTP-Referer": "https://github.com/sunxingshu/travel_plan_suns_family"},
    )


def _model() -> str:
    return os.environ.get("AI_MODEL", _DEFAULT_MODEL)


def _call_with_retry(client: OpenAI, messages: list[dict], label: str) -> str:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=_model(),
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            if attempt == _MAX_RETRIES:
                raise RuntimeError(f"AI call failed after {_MAX_RETRIES + 1} attempts ({label}): {e}") from e
            wait = _RETRY_DELAY * (attempt + 1)
            logger.warning("AI call attempt %d failed (%s): %s — retrying in %.0fs", attempt + 1, label, e, wait)
            time.sleep(wait)
    return ""


def _extract_json(text: str) -> str:
    """Extract the first JSON array or object from a response that may include prose."""
    # Try direct parse first
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        return stripped

    # Find JSON block inside markdown code fences
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", stripped)
    if fence:
        return fence.group(1).strip()

    # Find first [ or { and last ] or }
    start = min(
        (stripped.find("[") if stripped.find("[") >= 0 else len(stripped)),
        (stripped.find("{") if stripped.find("{") >= 0 else len(stripped)),
    )
    if start < len(stripped):
        # Find matching close
        return stripped[start:]

    return stripped


def suggest_destinations(
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
    client: OpenAI | None = None,
) -> list[dict]:
    """
    Asks the AI to suggest 3–5 destination + date combinations.
    Returns: [{"city": "...", "iata": "...", "country": "...", "best_departure": "YYYY-MM-DD",
               "best_return": "YYYY-MM-DD", "rationale": "..."}]
    """
    if client is None:
        client = _get_client()

    windows_text = "\n".join(
        f"  - Depart {dep}, Return {ret}" for dep, ret in candidate_windows[:10]
    )
    children_desc = (
        f"{len(config.children_ages)} children (ages {', '.join(str(a) for a in config.children_ages)})"
        if config.children_ages
        else "no children"
    )

    system_prompt = (
        "You are a travel expert. Respond ONLY with a valid JSON array. "
        "No prose, no markdown, no explanation outside the JSON."
    )
    user_prompt = f"""Suggest the 4 best travel destinations for a family trip.

FAMILY PROFILE:
- Home airport: {config.home_airport}
- Travelers: {config.adults} adults, {children_desc}
- Budget: ${config.budget_usd:,.0f} total (flights + hotel)
- Max one-way flight duration: {config.max_flight_hours} hours
- Interests: {', '.join(config.destination_interests)}
- Passports: {', '.join(config.passport_countries)}
- Visa-free destinations only: {config.visa_free_only}
- Exclude regions: {', '.join(config.exclude_regions) if config.exclude_regions else 'none'}

AVAILABLE TRAVEL WINDOWS (pick the best window per destination):
{windows_text}

REQUIREMENTS:
- Family-friendly destinations suitable for kids aged {', '.join(str(a) for a in config.children_ages)}
- Reachable within {config.max_flight_hours} hours from {config.home_airport}
- Total estimated cost (flights + hotel) should fit within ${config.budget_usd:,.0f}
- Consider current season and weather at travel time
- Vary the suggestions (different types: beach, city, nature, etc.)

Respond with a JSON array of exactly 4 objects:
[
  {{
    "city": "City Name",
    "iata": "XXX",
    "country": "Country Name",
    "best_departure": "YYYY-MM-DD",
    "best_return": "YYYY-MM-DD",
    "rationale": "2-sentence reason why this is great for this family at this time"
  }}
]

Only use departure/return dates from the available travel windows listed above."""

    raw = _call_with_retry(client, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], label="suggest_destinations")

    return _parse_destination_response(raw, candidate_windows)


def _parse_destination_response(raw: str, candidate_windows: list[tuple[str, str]]) -> list[dict]:
    json_str = _extract_json(raw)
    try:
        destinations = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse destination JSON: %s", raw[:300])
        return []

    if not isinstance(destinations, list):
        destinations = [destinations]

    valid = []
    valid_departures = {dep for dep, _ in candidate_windows}

    for d in destinations:
        if not all(k in d for k in ("city", "iata", "country")):
            continue
        iata = str(d.get("iata", "")).upper().strip()
        if len(iata) != 3:
            continue
        # Use AI-suggested dates if they're in our valid windows, else fall back to first window
        dep = d.get("best_departure", "")
        ret = d.get("best_return", "")
        if dep not in valid_departures and candidate_windows:
            dep, ret = candidate_windows[0]
        d["iata"] = iata
        d["best_departure"] = dep
        d["best_return"] = ret
        d.setdefault("rationale", "")
        valid.append(d)

    return valid[:5]


def synthesize_travel_plans(
    destination_plans: list[DestinationPlan],
    config: FamilyConfig,
    client: OpenAI | None = None,
) -> list[TravelPlan]:
    """
    Ranks destination plans and generates rich recommendation text for the top 3.
    Returns list[TravelPlan] sorted by rank.
    """
    if client is None:
        client = _get_client()

    plans_text = "\n\n".join(
        _format_destination_for_prompt(plan, config) for plan in destination_plans
    )
    children_desc = (
        f"kids aged {', '.join(str(a) for a in config.children_ages)}"
        if config.children_ages else "no children"
    )

    system_prompt = (
        "You are a family travel advisor. Respond ONLY with a valid JSON array. "
        "No prose, no markdown, no explanation outside the JSON."
    )
    user_prompt = f"""Rank the following travel destinations for a family and provide detailed recommendations.

FAMILY: {config.adults} adults, {children_desc} | Budget: ${config.budget_usd:,.0f} | Interests: {', '.join(config.destination_interests)}

DESTINATIONS WITH DATA:
{plans_text}

Rank the top 3 destinations (best first). For each provide:
- A warm, specific 2-3 sentence recommendation paragraph (mention the specific flight, hotel, and weather details)
- 3 pros and 2 cons specific to this family
- A "best for" label (e.g., "Beach & relaxation", "Cultural adventure", "Outdoor explorers")
- An overall score from 1.0 to 10.0

Scoring factors: total cost vs budget, weather quality, flight comfort (duration/stops), hotel quality/stars, child-friendliness, points redemption value. If data was missing for a destination, factor in that uncertainty.

Respond with a JSON array of exactly 3 objects:
[
  {{
    "rank": 1,
    "destination_city": "exact city name from above",
    "recommendation_text": "...",
    "pros": ["...", "...", "..."],
    "cons": ["...", "..."],
    "best_for": "...",
    "overall_score": 8.5
  }}
]"""

    raw = _call_with_retry(client, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], label="synthesize_plans")

    return _parse_synthesis_response(raw, destination_plans)


def _format_destination_for_prompt(plan: DestinationPlan, config: FamilyConfig) -> str:
    lines = [f"=== {plan.destination_city}, {plan.country} ({plan.destination_iata}) ==="]

    if plan.flight:
        f = plan.flight
        lines.append(
            f"FLIGHT: ${f.price_usd:,.0f} total | {f.airline} | {f.duration_hours}h | "
            f"{f.stops} stop(s) | Depart {f.departure_date} Return {f.return_date}"
        )
    else:
        lines.append("FLIGHT: Data unavailable")

    if plan.hotel:
        h = plan.hotel
        lines.append(
            f"HOTEL: {h.name} | {h.star_rating:.0f}-star | ${h.price_per_night_usd:,.0f}/night | "
            f"{h.nights} nights = ${h.total_price_usd:,.0f}"
        )
    else:
        lines.append("HOTEL: Data unavailable")

    if plan.weather:
        w = plan.weather
        forecast_note = "(5-day forecast)" if w.is_forecast else "(seasonal estimate)"
        lines.append(
            f"WEATHER: {w.avg_temp_celsius}°C avg | {w.conditions} | "
            f"{w.precipitation_chance_pct:.0f}% precipitation {forecast_note}"
        )
    else:
        lines.append("WEATHER: Data unavailable")

    if plan.award_availability:
        avail_lines = []
        for a in plan.award_availability[:3]:
            avail_lines.append(f"{a.program}: {a.seats_available} seats @ {a.points_required:,} pts ({a.cabin})")
        lines.append("AWARD SEATS: " + " | ".join(avail_lines))

    if plan.flight_points_options:
        best = plan.flight_points_options[0]
        lines.append(
            f"BEST FLIGHT POINTS: {best.program_name} — need {best.points_required:,} pts, "
            f"have {best.points_available:,} | {best.recommendation}"
        )

    if plan.hotel_points_options:
        best = plan.hotel_points_options[0]
        lines.append(
            f"BEST HOTEL POINTS: {best.program_name} — need {best.points_required:,} pts, "
            f"have {best.points_available:,} | {best.recommendation}"
        )

    lines.append(f"TOTAL CASH COST: ${plan.total_cash_cost_usd:,.0f}")

    if plan.data_errors:
        lines.append("DATA NOTES: " + "; ".join(plan.data_errors))

    return "\n".join(lines)


def _parse_synthesis_response(raw: str, destination_plans: list[DestinationPlan]) -> list[TravelPlan]:
    json_str = _extract_json(raw)
    try:
        ranked = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse synthesis JSON: %s", raw[:300])
        return _fallback_plans(destination_plans)

    if not isinstance(ranked, list):
        ranked = [ranked]

    # Build a lookup: city name → DestinationPlan
    plan_lookup = {p.destination_city.lower(): p for p in destination_plans}

    results: list[TravelPlan] = []
    for item in ranked[:3]:
        city_key = item.get("destination_city", "").lower()
        dest_plan = plan_lookup.get(city_key)
        if dest_plan is None:
            # Fuzzy match — find closest city name
            for key, plan in plan_lookup.items():
                if city_key in key or key in city_key:
                    dest_plan = plan
                    break
        if dest_plan is None and destination_plans:
            dest_plan = destination_plans[0]

        results.append(TravelPlan(
            rank=int(item.get("rank", len(results) + 1)),
            destination_plan=dest_plan,  # type: ignore[arg-type]
            ai_recommendation_text=item.get("recommendation_text", ""),
            pros=item.get("pros", []),
            cons=item.get("cons", []),
            best_for=item.get("best_for", ""),
            overall_score=float(item.get("overall_score", 7.0)),
        ))

    results.sort(key=lambda p: p.rank)
    return results


def _fallback_plans(destination_plans: list[DestinationPlan]) -> list[TravelPlan]:
    """Minimal fallback when AI synthesis fails — return top 3 by lowest cost."""
    sorted_plans = sorted(
        [p for p in destination_plans if p.flight or p.hotel],
        key=lambda p: p.total_cash_cost_usd,
    )
    return [
        TravelPlan(
            rank=i + 1,
            destination_plan=plan,
            ai_recommendation_text="AI synthesis unavailable — ranked by total cost.",
            pros=[],
            cons=[],
            best_for="",
            overall_score=0.0,
        )
        for i, plan in enumerate(sorted_plans[:3])
    ]
