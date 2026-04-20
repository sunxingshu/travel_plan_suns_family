from src.models import FamilyConfig, FlightOption, HotelOption, PointsRedemption

# Cents-per-point benchmarks (industry averages, last reviewed 2026-04)
# Sources: The Points Guy, NerdWallet valuations — update annually.
_AIRLINE_CPP: dict[str, float] = {
    "United MileagePlus":        1.35,
    "Delta SkyMiles":            1.20,
    "American AAdvantage":       1.77,
    "Southwest Rapid Rewards":   1.50,
    "Alaska Mileage Plan":       1.80,
    "JetBlue TrueBlue":          1.30,
    "default":                   1.20,
}

_HOTEL_CPP: dict[str, float] = {
    "Marriott Bonvoy":    0.84,
    "Hilton Honors":      0.60,
    "World of Hyatt":     1.70,
    "IHG One Rewards":    0.50,
    "Wyndham Rewards":    0.90,
    "default":            0.70,
}

# Airline IATA → common loyalty program name (for program-match detection)
_AIRLINE_IATA_TO_PROGRAM: dict[str, str] = {
    "UA": "United MileagePlus",
    "DL": "Delta SkyMiles",
    "AA": "American AAdvantage",
    "WN": "Southwest Rapid Rewards",
    "AS": "Alaska Mileage Plan",
    "B6": "JetBlue TrueBlue",
}

# Hotel brand keyword → loyalty program (for program-match detection)
_HOTEL_BRAND_KEYWORDS: dict[str, str] = {
    "hyatt":    "World of Hyatt",
    "marriott": "Marriott Bonvoy",
    "westin":   "Marriott Bonvoy",
    "sheraton": "Marriott Bonvoy",
    "hilton":   "Hilton Honors",
    "hampton":  "Hilton Honors",
    "doubletree": "Hilton Honors",
    "ihg":      "IHG One Rewards",
    "holiday inn": "IHG One Rewards",
    "intercontinental": "IHG One Rewards",
    "wyndham":  "Wyndham Rewards",
    "days inn": "Wyndham Rewards",
}


def _redemption_quality(cpp: float, benchmark: float) -> str:
    ratio = cpp / benchmark
    if ratio >= 1.3:
        return "Excellent value — well above average"
    if ratio >= 1.0:
        return "Good value — at or above benchmark"
    if ratio >= 0.8:
        return "Acceptable — slightly below benchmark"
    return "Below average — consider paying cash"


def _calculate_redemption(
    program: str,
    cpp_table: dict[str, float],
    points_available: int,
    cash_value_usd: float,
    program_match: bool,
) -> PointsRedemption:
    cpp = cpp_table.get(program, cpp_table["default"])
    benchmark = cpp  # We use benchmark CPP as the target; achieved CPP equals benchmark at face value
    points_required = round(cash_value_usd / (cpp / 100))
    cash_value_of_balance = round(points_available * (cpp / 100), 2)
    can_cover_fully = points_available >= points_required
    can_cover_partially = (not can_cover_fully) and points_available >= points_required * 0.4

    recommendation = _redemption_quality(cpp, benchmark)
    if program_match:
        recommendation += " | Program match — best award availability"
    if can_cover_fully:
        recommendation += f" | Can fully cover (${cash_value_usd:.0f} cash → {points_required:,} pts)"
    elif can_cover_partially:
        pts_short = points_required - points_available
        recommendation += f" | Partial coverage — need {pts_short:,} more pts"

    return PointsRedemption(
        program_name=program,
        points_available=points_available,
        points_required=points_required,
        cash_value_usd=cash_value_of_balance,
        cpp=cpp,
        cpp_benchmark=benchmark,
        can_cover_fully=can_cover_fully,
        can_cover_partially=can_cover_partially,
        recommendation=recommendation,
        program_match=program_match,
    )


def find_best_airline_redemptions(
    config: FamilyConfig, flight: FlightOption
) -> list[PointsRedemption]:
    results = []
    for airline in config.airlines:
        program_match = (
            _AIRLINE_IATA_TO_PROGRAM.get(flight.airline_iata, "").lower()
            == airline.loyalty_program.lower()
        )
        r = _calculate_redemption(
            program=airline.loyalty_program,
            cpp_table=_AIRLINE_CPP,
            points_available=airline.points_balance,
            cash_value_usd=flight.price_usd,
            program_match=program_match,
        )
        results.append(r)
    # Sort: program matches first, then by points coverage, then by CPP
    results.sort(key=lambda r: (not r.program_match, not r.can_cover_fully, -r.cpp))
    return results


def find_best_hotel_redemptions(
    config: FamilyConfig, hotel: HotelOption
) -> list[PointsRedemption]:
    hotel_name_lower = hotel.name.lower()
    results = []
    for brand in config.hotel_brands:
        program_match = any(
            kw in hotel_name_lower
            for kw, prog in _HOTEL_BRAND_KEYWORDS.items()
            if prog.lower() == brand.loyalty_program.lower()
        )
        r = _calculate_redemption(
            program=brand.loyalty_program,
            cpp_table=_HOTEL_CPP,
            points_available=brand.points_balance,
            cash_value_usd=hotel.total_price_usd,
            program_match=program_match,
        )
        results.append(r)
    results.sort(key=lambda r: (not r.program_match, not r.can_cover_fully, -r.cpp))
    return results
