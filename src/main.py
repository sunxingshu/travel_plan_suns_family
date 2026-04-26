from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

from src.ai_planner import suggest_destinations, synthesize_travel_plans
from src.award_search import get_award_availability
from src.balance_sync import sync_balances
from src.config_loader import generate_candidate_windows, load_config
from src.email_sender import EmailSendError, send_failure_email, send_travel_email
from src.flights import get_best_flight
from src.hotels import get_best_hotel
from src.loyalty_calculator import (
    find_best_airline_redemptions,
    find_best_hotel_redemptions,
    get_best_cc_transfer,
)
from src.models import DestinationPlan, FamilyConfig, TravelPlan, DreamPlan
from src.weather import get_weather_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("travel_planner.log"),
    ],
)
logger = logging.getLogger("travel_planner")


def main() -> None:
    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    logger.info("=== Travel planner run starting: %s ===", run_date)

    gmail_email = _require_env("GMAIL_EMAIL")
    gmail_password = _require_env("GMAIL_APP_PASSWORD")

    try:
        _run_pipeline(run_date, gmail_email, gmail_password)
    except Exception as e:
        logger.exception("Pipeline failed: %s", e)
        try:
            config = load_config("family_preferences.yaml")
            send_failure_email(gmail_email, gmail_password, config.notification_emails, str(e), run_date)
        except Exception:
            pass
        sys.exit(1)


def _run_pipeline(run_date: str, gmail_email: str, gmail_password: str) -> None:
    from src.ai_planner import select_destinations_from_deals
    from src.flights import get_all_cheap_flights
    from src.amadeus_auth import is_configured as amadeus_configured

    # --- Load secrets ---
    openweather_key = _require_env("OPENWEATHER_API_KEY")

    # --- Load config ---
    config = load_config("family_preferences.yaml")
    logger.info(
        "Config loaded. Home: %s | Adults: %d | Children: %s | Budget: $%.0f | Credit cards: %d",
        config.home_airport, config.adults, config.children_ages, config.budget_usd,
        len(config.credit_cards),
    )

    # --- Optional: sync live balances from AwardWallet ---
    updated_count, sync_msgs = sync_balances(config)
    if updated_count:
        logger.info("AwardWallet synced %d account(s):", updated_count)
        for msg in sync_msgs:
            logger.info("  %s", msg)
    elif os.environ.get("AWARDWALLET_TOKEN"):
        logger.info("AwardWallet: token present but no matching accounts found")
    else:
        logger.info("AwardWallet: no token set — using balances from family_preferences.yaml")

    # --- Generate travel windows ---
    candidate_windows = generate_candidate_windows(config)
    if not candidate_windows:
        raise RuntimeError(
            "No valid travel windows found. Check availability settings in family_preferences.yaml."
        )
    logger.info("Generated %d candidate travel windows (next %d months)",
                len(candidate_windows), config.availability.lookahead_months)

    # --- Log available providers ---
    providers = []
    if os.environ.get("SERPAPI_API_KEY", "").strip():
        providers.append("SerpAPI")
    try:
        if amadeus_configured():
            providers.append("Amadeus")
    except Exception:
        pass
    if os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
        providers.append("Travelpayouts")
    if os.environ.get("KIWI_API_KEY", "").strip():
        providers.append("Kiwi")
    logger.info("Flight providers available: %s", ", ".join(providers) if providers else "NONE — check API keys!")

    # --- Step 1: Broad flight discovery ---
    logger.info("Fetching broad flight deals from %s across all destinations...", config.home_airport)
    all_cheap_flights = get_all_cheap_flights(config, candidate_windows)

    # Filter by budget only (duration is handled by category split below)
    budget_filtered = [
        f for f in all_cheap_flights
        if f.price_usd <= config.budget_usd * 0.65  # Leave room for hotel
    ]

    # Split into short-haul (≤5h) and long-haul (>5h) categories
    short_haul = [f for f in budget_filtered if f.duration_hours <= 5.0]
    long_haul = [f for f in budget_filtered if f.duration_hours > 5.0]
    logger.info(
        "Found %d total deals | %d within budget | Short-haul (≤5h): %d | Long-haul (>5h): %d",
        len(all_cheap_flights), len(budget_filtered), len(short_haul), len(long_haul),
    )

    # --- Step 2: AI selects best destinations from real deals ---
    ai_model = os.environ.get("AI_MODEL", "").strip() or "deepseek/deepseek-r1"
    logger.info("Using AI model: %s | key=%s", ai_model, ("set" if os.environ.get("OPENROUTER_API_KEY", "").strip() else "MISSING"))

    destinations = []
    flight_lookup: dict = {}

    # Select from short-haul deals
    if len(short_haul) >= 3:
        logger.info("AI selecting 3 short-haul destinations from %d deals...", len(short_haul))
        short_dests = select_destinations_from_deals(short_haul[:25], config, category="short-haul (≤5h)")
        for d in short_dests[:3]:
            d["category"] = "Short-Haul (≤5h Flight)"
        destinations.extend(short_dests[:3])

    # Select from long-haul deals
    if len(long_haul) >= 3:
        logger.info("AI selecting 3 long-haul/international destinations from %d deals...", len(long_haul))
        long_dests = select_destinations_from_deals(long_haul[:25], config, category="long-haul/international (>5h)")
        for d in long_dests[:3]:
            d["category"] = "International / Long-Haul (>5h Flight)"
        destinations.extend(long_dests[:3])

    flight_lookup = {f.destination: f for f in all_cheap_flights}

    # Fallback if we don't have enough from either category
    if len(destinations) < 3:
        logger.warning("Only %d destinations from deal-based selection — falling back to AI suggestion", len(destinations))
        fallback = suggest_destinations(config, candidate_windows)
        for d in fallback:
            d.setdefault("category", "AI Suggested")
        destinations.extend(fallback)
        flight_lookup = {f.destination: f for f in all_cheap_flights}

    if not destinations:
        raise RuntimeError("Could not identify destination candidates.")
    logger.info("Selected destinations: %s", [d["city"] for d in destinations])

    # --- Step 3: Enrich each destination with hotel + weather ---
    destination_plans: list[DestinationPlan] = []
    for dest in destinations:
        logger.info("Gathering hotel + weather for %s, %s...", dest["city"], dest["country"])
        plan = _gather_destination_data(
            dest, config, candidate_windows, openweather_key, flight_lookup
        )
        _add_loyalty_calculations(plan, config)
        destination_plans.append(plan)
        logger.info(
            "  %s: flight=$%s, hotel=$%s, weather=%s, errors=%d",
            dest["city"],
            f"{plan.flight.price_usd:.0f}" if plan.flight else "none",
            f"{plan.hotel.total_price_usd:.0f}" if plan.hotel else "none",
            "yes" if plan.weather else "no",
            len(plan.data_errors),
        )

    viable = [p for p in destination_plans if p.flight or p.hotel or p.weather]
    if len(viable) < 3:
        raise RuntimeError(
            f"Only {len(viable)} destination(s) had any data (need at least 3). "
            "Check API credentials in GitHub Secrets."
        )

    # --- Step 4: AI synthesizes top recommendations with full data ---
    logger.info("Synthesizing top %d recommendations from real flight + hotel data...", len(viable))
    travel_plans = synthesize_travel_plans(viable, config)
    if not travel_plans:
        raise RuntimeError("AI synthesis returned no travel plans.")

    # Group by category: short-haul first, then long-haul, maintaining rank within each group
    def _category_sort(p):
        cat = (p.category or "").lower()
        if "short" in cat:
            return (0, p.rank)
        elif "long" in cat or "international" in cat:
            return (1, p.rank)
        return (2, p.rank)

    travel_plans.sort(key=_category_sort)
    # Re-number ranks within each category
    for i, plan in enumerate(travel_plans, 1):
        plan.rank = i

    logger.info("Top pick: %s (score: %.1f)", travel_plans[0].destination_plan.destination_city,
                travel_plans[0].overall_score)

    # --- Step 4.5: Dream Destinations Check ---
    logger.info("Checking cheapest rates for Dream Destinations...")
    from src.flights import get_dream_flights
    dream_flights = get_dream_flights(config)
    dream_plans: list[DreamPlan] = []
    
    # Map flights to dreams
    flight_by_iata = {f.destination: f for f in dream_flights}
    for dream in config.dream_destinations:
        f = flight_by_iata.get(dream.iata)
        dp = DreamPlan(dream=dream, flight=f)
        if f:
            # Figure out points
            from src.loyalty_calculator import find_best_airline_redemptions, get_best_cc_transfer
            flight_pts = find_best_airline_redemptions(config, f)
            dp.flight_points_options = [p for p in flight_pts if not p.via_transfer]
            dp.best_cc_transfer_flight = get_best_cc_transfer(flight_pts)
        dream_plans.append(dp)

    # --- Step 4.6: Transfer Bonuses ---
    from src.transfer_bonuses import get_active_transfer_bonuses, EVERGREEN_TIPS
    transfer_bonuses = get_active_transfer_bonuses()
    logger.info("Active transfer bonuses: %d", len(transfer_bonuses))

    # --- Step 5: Send email ---
    logger.info("Sending email to %s...", ", ".join(config.notification_emails))
    send_travel_email(
        gmail_email=gmail_email,
        gmail_app_password=gmail_password,
        recipient_emails=config.notification_emails,
        travel_plans=travel_plans,
        dream_plans=dream_plans,
        transfer_bonuses=transfer_bonuses,
        evergreen_tips=EVERGREEN_TIPS,
        config=config,
        run_date=run_date,
    )
    logger.info("=== Run complete. Email delivered. ===")


def _gather_destination_data(
    dest: dict,
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
    openweather_key: str,
    flight_lookup: dict | None = None,
) -> DestinationPlan:
    errors: list[str] = []

    # Use the pre-fetched flight from the broad search — it already represents the
    # cheapest price found across multiple windows for this specific destination.
    flight = None
    if flight_lookup:
        flight = flight_lookup.get(dest["iata"])

    if flight is None:
        # Fallback: broad search didn't cover this destination, search individually.
        ai_dep = dest.get("best_departure", "")
        ai_ret = dest.get("best_return", "")
        valid_deps = {dep for dep, _ in candidate_windows}
        if ai_dep in valid_deps:
            preferred_windows = [(ai_dep, ai_ret)] + [w for w in candidate_windows if w[0] != ai_dep][:2]
        elif ai_dep:
            preferred_windows = [(ai_dep, ai_ret)] + candidate_windows[:2]
        else:
            preferred_windows = candidate_windows[:3]

        try:
            flight = get_best_flight(config, dest["iata"], preferred_windows)
            if flight is None:
                errors.append("No flights found within duration/price constraints")
        except Exception as e:
            errors.append(f"Flight search error: {e}")

    # Derive check-in/out dates
    if flight:
        check_in, check_out = flight.departure_date, flight.return_date
    elif dest.get("best_departure"):
        check_in, check_out = dest["best_departure"], dest["best_return"]
    elif candidate_windows:
        check_in, check_out = candidate_windows[0]
    else:
        errors.append("No dates available")
        check_in = check_out = ""

    # Hotel
    hotel = None
    if check_in and check_out:
        try:
            hotel = get_best_hotel(config, dest["iata"], check_in, check_out,
                                   city_name=dest.get("city", ""))
            if hotel is None:
                errors.append("No hotels found matching preferences")
        except Exception as e:
            errors.append(f"Hotel search error: {e}")

    # Weather
    weather = None
    if check_in and check_out:
        weather = get_weather_summary(
            openweather_key, dest["city"], dest["country"], check_in, check_out
        )
        if weather is None:
            errors.append("Weather data unavailable")

    # Award availability (non-blocking)
    awards = []
    if check_in:
        awards = get_award_availability(config, config.home_airport, dest["iata"], check_in,
                                        check_out if check_out else check_in)

    total = (flight.price_usd if flight else 0) + (hotel.total_price_usd if hotel else 0)

    return DestinationPlan(
        destination_city=dest["city"],
        destination_iata=dest["iata"],
        country=dest["country"],
        suggested_rationale=dest.get("rationale", ""),
        flight=flight,
        hotel=hotel,
        weather=weather,
        award_availability=awards,
        total_cash_cost_usd=total,
        data_errors=errors,
        category=dest.get("category", ""),
    )


def _add_loyalty_calculations(plan: DestinationPlan, config: FamilyConfig) -> None:
    if plan.flight:
        plan.flight_points_options = find_best_airline_redemptions(config, plan.flight)
        plan.best_cc_transfer_flight = get_best_cc_transfer(plan.flight_points_options)
    if plan.hotel:
        plan.hotel_points_options = find_best_hotel_redemptions(config, plan.hotel)
        plan.best_cc_transfer_hotel = get_best_cc_transfer(plan.hotel_points_options)


def _require_env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "Add it to GitHub Secrets (repo → Settings → Secrets and variables → Actions)."
        )
    return val


if __name__ == "__main__":
    main()
