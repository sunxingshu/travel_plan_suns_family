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
from src.models import DestinationPlan, FamilyConfig
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
            send_failure_email(gmail_email, gmail_password, config.notification_email, str(e), run_date)
        except Exception:
            pass
        sys.exit(1)


def _run_pipeline(run_date: str, gmail_email: str, gmail_password: str) -> None:
    # --- Load secrets ---
    openweather_key = _require_env("OPENWEATHER_API_KEY")

    # Amadeus client (optional — falls back gracefully if not configured)
    amadeus_client = _init_amadeus()

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
            "No valid travel windows found. Check availability.pattern, exclude_dates, "
            "and preferred_travel_windows in family_preferences.yaml."
        )
    logger.info("Generated %d candidate travel windows (next %d months)", len(candidate_windows), config.availability.lookahead_months)

    # --- Step 1: AI suggests destinations ---
    logger.info("Requesting destination suggestions from AI (%s)...", os.environ.get("AI_MODEL", "deepseek/deepseek-r1:free"))
    destinations = suggest_destinations(config, candidate_windows)
    if not destinations:
        raise RuntimeError("AI returned no valid destinations.")
    logger.info("AI suggested: %s", [d["city"] for d in destinations])

    # --- Step 2: Gather real data for each candidate ---
    destination_plans: list[DestinationPlan] = []
    for dest in destinations:
        logger.info("Gathering data for %s, %s...", dest["city"], dest["country"])
        plan = _gather_destination_data(
            dest, config, candidate_windows, amadeus_client, openweather_key
        )
        _add_loyalty_calculations(plan, config)
        destination_plans.append(plan)
        logger.info(
            "  %s: flight=%s, hotel=%s, weather=%s, awards=%d, errors=%d",
            dest["city"],
            f"${plan.flight.price_usd:.0f}" if plan.flight else "none",
            f"${plan.hotel.total_price_usd:.0f}" if plan.hotel else "none",
            "yes" if plan.weather else "no",
            len(plan.award_availability),
            len(plan.data_errors),
        )

    viable = [p for p in destination_plans if p.flight or p.hotel]
    if len(viable) < 2:
        raise RuntimeError(
            f"Only {len(viable)} destination(s) had usable data (need at least 2). "
            "Check API credentials in GitHub Secrets."
        )

    # --- Step 3: AI synthesizes top 3 ---
    logger.info("Requesting synthesis from AI...")
    travel_plans = synthesize_travel_plans(viable, config)
    if not travel_plans:
        raise RuntimeError("AI synthesis returned no travel plans.")
    logger.info("Synthesis complete. Top pick: %s", travel_plans[0].destination_plan.destination_city)

    # --- Step 4: Send email ---
    logger.info("Sending email to %s...", config.notification_email)
    send_travel_email(
        gmail_email=gmail_email,
        gmail_app_password=gmail_password,
        recipient_email=config.notification_email,
        travel_plans=travel_plans,
        config=config,
        run_date=run_date,
    )
    logger.info("=== Run complete. Email delivered. ===")


def _gather_destination_data(
    dest: dict,
    config: FamilyConfig,
    candidate_windows: list[tuple[str, str]],
    amadeus_client,
    openweather_key: str,
) -> DestinationPlan:
    errors: list[str] = []

    # Prefer AI-suggested dates; fall back to first candidate window
    ai_dep = dest.get("best_departure", "")
    ai_ret = dest.get("best_return", "")
    valid_deps = {dep for dep, _ in candidate_windows}
    if ai_dep in valid_deps:
        preferred_windows = [(ai_dep, ai_ret)] + [w for w in candidate_windows if w[0] != ai_dep][:2]
    else:
        preferred_windows = candidate_windows[:3]

    # Flight
    flight = None
    try:
        flight = get_best_flight(config, dest["iata"], preferred_windows, amadeus_client)
        if flight is None:
            errors.append("No flights found within duration/price constraints")
    except Exception as e:
        errors.append(f"Flight search error: {e}")

    # Derive check-in/out dates
    if flight:
        check_in, check_out = flight.departure_date, flight.return_date
    else:
        check_in, check_out = preferred_windows[0]

    # Hotel
    hotel = None
    try:
        hotel = get_best_hotel(config, dest["iata"], check_in, check_out, amadeus_client)
        if hotel is None:
            errors.append("No hotels found matching preferences")
    except Exception as e:
        errors.append(f"Hotel search error: {e}")

    # Weather
    weather = get_weather_summary(
        openweather_key, dest["city"], dest["country"], check_in, check_out
    )
    if weather is None:
        errors.append("Weather data unavailable")

    # Award availability (non-blocking)
    awards = get_award_availability(config, config.home_airport, dest["iata"], check_in, check_out)

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
    )


def _add_loyalty_calculations(plan: DestinationPlan, config: FamilyConfig) -> None:
    if plan.flight:
        plan.flight_points_options = find_best_airline_redemptions(config, plan.flight)
        plan.best_cc_transfer_flight = get_best_cc_transfer(plan.flight_points_options)
    if plan.hotel:
        plan.hotel_points_options = find_best_hotel_redemptions(config, plan.hotel)
        plan.best_cc_transfer_hotel = get_best_cc_transfer(plan.hotel_points_options)


def _init_amadeus():
    client_id = os.environ.get("AMADEUS_CLIENT_ID", "")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logger.warning("Amadeus credentials not set — hotel/flight searches will use fallback providers")
        return None
    try:
        from amadeus import Client
        return Client(client_id=client_id, client_secret=client_secret)
    except Exception as e:
        logger.warning("Failed to initialize Amadeus client: %s", e)
        return None


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
