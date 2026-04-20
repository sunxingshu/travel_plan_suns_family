import yaml
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from src.models import FamilyConfig, AvailabilityConfig, AirlineLoyalty, HotelBrand

# US school holiday windows (approximate — adjust as needed for your school district)
_SCHOOL_HOLIDAY_WINDOWS = [
    # Summer
    (6, 15, 8, 20),   # (start_month, start_day, end_month, end_day)
    # Winter break
    (12, 20, 1, 5),
    # Spring break (mid-March to mid-April)
    (3, 15, 4, 15),
    # Thanksgiving week
    (11, 22, 11, 30),
]


def load_config(path: str = "family_preferences.yaml") -> FamilyConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    fam = raw["family"]
    trip = raw["trip"]
    avail = raw["availability"]
    dest = raw["destination_preferences"]
    hotels = raw["hotels"]
    airlines_raw = raw["airlines"]
    notif = raw["notifications"]

    home_airport = fam["home_airport"].upper().strip()
    if len(home_airport) != 3 or not home_airport.isalpha():
        raise ValueError(f"Invalid home_airport IATA code: '{home_airport}'. Must be 3 letters.")

    availability = AvailabilityConfig(
        pattern=avail["pattern"],
        exclude_dates=avail.get("exclude_dates", []),
        lookahead_months=avail.get("lookahead_months", 6),
        book_at_least_weeks_ahead=avail.get("book_at_least_weeks_ahead", 3),
    )

    valid_patterns = {"school_holidays_and_weekends", "anytime", "weekends_only"}
    if availability.pattern not in valid_patterns:
        raise ValueError(
            f"availability.pattern must be one of {valid_patterns}, got: '{availability.pattern}'"
        )

    hotel_brands = [
        HotelBrand(
            name=h["name"],
            loyalty_program=h["loyalty_program"],
            points_balance=int(h["points_balance"]),
        )
        for h in hotels["preferred_brands"]
    ]

    airline_list = [
        AirlineLoyalty(
            name=a["name"],
            iata_code=a["iata_code"].upper(),
            loyalty_program=a["loyalty_program"],
            points_balance=int(a["points_balance"]),
        )
        for a in airlines_raw
    ]

    return FamilyConfig(
        home_airport=home_airport,
        adults=int(fam["adults"]),
        children_ages=list(fam.get("children_ages", [])),
        trip_duration_min=int(trip["duration_min_days"]),
        trip_duration_max=int(trip["duration_max_days"]),
        budget_usd=float(trip["budget_usd"]),
        max_flight_hours=float(trip["max_flight_hours"]),
        availability=availability,
        destination_interests=dest.get("interests", []),
        visa_free_only=bool(dest.get("visa_free_only", True)),
        passport_countries=dest.get("passport_countries", ["US"]),
        exclude_regions=dest.get("exclude_regions", []),
        hotel_star_min=int(hotels.get("star_rating_min", 3)),
        hotel_brands=hotel_brands,
        airlines=airline_list,
        notification_email=notif["email"],
    )


def generate_candidate_windows(config: FamilyConfig) -> list[tuple[str, str]]:
    """
    Returns a list of (departure_date, return_date) string pairs (ISO format)
    that fall within the family's availability pattern, avoid excluded dates,
    and are at least book_at_least_weeks_ahead from today.
    """
    today = date.today()
    earliest_departure = today + timedelta(weeks=config.availability.book_at_least_weeks_ahead)
    latest_departure = today + relativedelta(months=config.availability.lookahead_months)
    exclude = set(config.availability.exclude_dates)
    duration_mid = (config.trip_duration_min + config.trip_duration_max) // 2

    candidates: list[tuple[str, str]] = []
    current = earliest_departure

    while current <= latest_departure:
        if _date_fits_pattern(current, config.availability.pattern):
            departure_str = current.isoformat()
            return_date = current + timedelta(days=duration_mid)
            return_str = return_date.isoformat()

            # Check neither departure nor return falls on an excluded date
            days_in_trip = [
                (current + timedelta(days=i)).isoformat()
                for i in range(duration_mid + 1)
            ]
            if not any(d in exclude for d in days_in_trip):
                candidates.append((departure_str, return_str))

        current += timedelta(days=1)

    # Deduplicate — keep one window per week to avoid too many near-identical options
    return _deduplicate_windows(candidates)


def _date_fits_pattern(d: date, pattern: str) -> bool:
    if pattern == "anytime":
        return True
    if pattern == "weekends_only":
        return d.weekday() == 5  # Saturday departures only
    if pattern == "school_holidays_and_weekends":
        if d.weekday() == 5:  # Saturday
            return True
        return _in_school_holiday(d)
    return False


def _in_school_holiday(d: date) -> bool:
    year = d.year
    for start_m, start_day, end_m, end_day in _SCHOOL_HOLIDAY_WINDOWS:
        # Handle year wrap for winter break (Dec → Jan)
        if start_m > end_m:
            start = date(year, start_m, start_day)
            end = date(year + 1, end_m, end_day)
        else:
            start = date(year, start_m, start_day)
            end = date(year, end_m, end_day)
        if start <= d <= end:
            return True
    return False


def _deduplicate_windows(
    candidates: list[tuple[str, str]], gap_days: int = 7
) -> list[tuple[str, str]]:
    """Keep at most one window per gap_days period to limit API call count."""
    if not candidates:
        return []
    result = [candidates[0]]
    for dep, ret in candidates[1:]:
        last_dep = date.fromisoformat(result[-1][0])
        this_dep = date.fromisoformat(dep)
        if (this_dep - last_dep).days >= gap_days:
            result.append((dep, ret))
    return result
