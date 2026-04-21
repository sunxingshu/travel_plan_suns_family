from src.models import CreditCard, FamilyConfig, FlightOption, HotelOption, PointsRedemption

# Cents-per-point benchmarks (industry averages, last reviewed 2026-04)
# Sources: The Points Guy, NerdWallet valuations — update annually.
_AIRLINE_CPP: dict[str, float] = {
    "United MileagePlus":        1.35,
    "Delta SkyMiles":            1.20,
    "American AAdvantage":       1.77,
    "Southwest Rapid Rewards":   1.50,
    "Alaska Mileage Plan":       1.80,
    "JetBlue TrueBlue":          1.30,
    "British Airways Avios":     1.50,
    "Air Canada Aeroplan":       1.55,
    "Flying Blue (Air France/KLM)": 1.35,
    "Virgin Atlantic Flying Club": 1.55,
    "Singapore KrisFlyer":       1.30,
    "Cathay Pacific Asia Miles": 1.40,
    "Turkish Miles&Smiles":      1.40,
    "default":                   1.20,
}

_HOTEL_CPP: dict[str, float] = {
    "World of Hyatt":     1.70,
    "Marriott Bonvoy":    0.84,
    "Hilton Honors":      0.60,
    "IHG One Rewards":    0.50,
    "Wyndham Rewards":    0.90,
    "Accor Live Limitless": 0.65,
    "Choice Privileges":  0.60,
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
    "BA": "British Airways Avios",
    "AC": "Air Canada Aeroplan",
    "AF": "Flying Blue (Air France/KLM)",
    "KL": "Flying Blue (Air France/KLM)",
    "VS": "Virgin Atlantic Flying Club",
    "SQ": "Singapore KrisFlyer",
    "CX": "Cathay Pacific Asia Miles",
    "TK": "Turkish Miles&Smiles",
}

# Hotel brand keyword → loyalty program (for program-match detection)
_HOTEL_BRAND_KEYWORDS: dict[str, str] = {
    "hyatt":           "World of Hyatt",
    "park hyatt":      "World of Hyatt",
    "andaz":           "World of Hyatt",
    "alila":           "World of Hyatt",
    "marriott":        "Marriott Bonvoy",
    "westin":          "Marriott Bonvoy",
    "sheraton":        "Marriott Bonvoy",
    "w hotel":         "Marriott Bonvoy",
    "ritz-carlton":    "Marriott Bonvoy",
    "st. regis":       "Marriott Bonvoy",
    "four points":     "Marriott Bonvoy",
    "courtyard":       "Marriott Bonvoy",
    "hilton":          "Hilton Honors",
    "hampton":         "Hilton Honors",
    "doubletree":      "Hilton Honors",
    "curio":           "Hilton Honors",
    "waldorf":         "Hilton Honors",
    "embassy suites":  "Hilton Honors",
    "ihg":             "IHG One Rewards",
    "holiday inn":     "IHG One Rewards",
    "intercontinental": "IHG One Rewards",
    "crowne plaza":    "IHG One Rewards",
    "kimpton":         "IHG One Rewards",
    "wyndham":         "Wyndham Rewards",
    "days inn":        "Wyndham Rewards",
    "ramada":          "Wyndham Rewards",
    "super 8":         "Wyndham Rewards",
    "accor":           "Accor Live Limitless",
    "novotel":         "Accor Live Limitless",
    "sofitel":         "Accor Live Limitless",
    "mercure":         "Accor Live Limitless",
    "choice":          "Choice Privileges",
    "comfort inn":     "Choice Privileges",
    "quality inn":     "Choice Privileges",
}

# Credit card transfer partners.
# Each entry: {target_program: ratio} where ratio = how many target points per source point.
# e.g. 1.0 = 1:1, 0.75 = 2:1.5 (Capital One → some partners)
_CC_TRANSFER_PARTNERS: dict[str, dict[str, float]] = {
    "Chase Ultimate Rewards": {
        # Airlines (all 1:1)
        "United MileagePlus":          1.0,
        "Southwest Rapid Rewards":     1.0,
        "British Airways Avios":       1.0,
        "Air Canada Aeroplan":         1.0,
        "Singapore KrisFlyer":         1.0,
        "JetBlue TrueBlue":            1.0,
        "Flying Blue (Air France/KLM)": 1.0,
        "Virgin Atlantic Flying Club": 1.0,
        "Iberia Avios":                1.0,
        "Emirates Skywards":           1.0,
        # Hotels (all 1:1)
        "World of Hyatt":              1.0,
        "Marriott Bonvoy":             1.0,
        "IHG One Rewards":             1.0,
        "Wyndham Rewards":             1.0,
    },
    "Amex Membership Rewards": {
        # Airlines
        "Delta SkyMiles":              1.0,
        "United MileagePlus":          1.0,
        "British Airways Avios":       1.0,
        "Air Canada Aeroplan":         1.0,
        "Singapore KrisFlyer":         1.0,
        "Flying Blue (Air France/KLM)": 1.0,
        "Virgin Atlantic Flying Club": 1.0,
        "Cathay Pacific Asia Miles":   1.0,
        "Turkish Miles&Smiles":        1.0,
        "Iberia Avios":                1.0,
        "JetBlue TrueBlue":            0.8,   # 5:4
        "Emirates Skywards":           0.8,   # 5:4
        "Aeromexico Club Premier":     1.6,   # 1:1.6 (great ratio)
        # Hotels
        "Hilton Honors":               2.0,   # 1:2 — lots of Hilton points per MR
        "Marriott Bonvoy":             1.0,
    },
    "Capital One Venture Miles": {
        # Airlines — most 1:1, some reduced
        "Air Canada Aeroplan":         1.0,
        "British Airways Avios":       1.0,
        "Flying Blue (Air France/KLM)": 1.0,
        "Turkish Miles&Smiles":        1.0,
        "Singapore KrisFlyer":         1.0,
        "Cathay Pacific Asia Miles":   1.0,
        "Avianca LifeMiles":           1.0,
        "JetBlue TrueBlue":            0.6,   # 5:3
        "Emirates Skywards":           0.75,  # 2:1.5
        "Japan Airlines Mileage Bank": 0.75,  # 2:1.5
        # Hotels
        "Wyndham Rewards":             1.0,
        "Choice Privileges":           1.0,
        "Accor Live Limitless":        0.5,   # 2:1
    },
    "Citi ThankYou Points": {
        # Airlines
        "American AAdvantage":         1.0,
        "Singapore KrisFlyer":         1.0,
        "Turkish Miles&Smiles":        1.0,
        "Flying Blue (Air France/KLM)": 1.0,
        "Virgin Atlantic Flying Club": 1.0,
        "Cathay Pacific Asia Miles":   1.0,
        "Avianca LifeMiles":           1.0,
        # Hotels
        "Wyndham Rewards":             1.0,
        "Choice Privileges":           1.0,
        "Accor Live Limitless":        1.0,
    },
    "Bilt Points": {
        # Airlines (almost all 1:1 — uniquely broad program)
        "United MileagePlus":          1.0,
        "American AAdvantage":         1.0,
        "Alaska Mileage Plan":         1.0,
        "Southwest Rapid Rewards":     1.0,
        "Air Canada Aeroplan":         1.0,
        "British Airways Avios":       1.0,
        "Flying Blue (Air France/KLM)": 1.0,
        "Virgin Atlantic Flying Club": 1.0,
        "Cathay Pacific Asia Miles":   1.0,
        "Turkish Miles&Smiles":        1.0,
        "Emirates Skywards":           1.0,   # rare 1:1
        # Hotels
        "World of Hyatt":              1.0,
        "Marriott Bonvoy":             1.0,
        "Hilton Honors":               1.0,
        "IHG One Rewards":             1.0,
        "Accor Live Limitless":        0.667, # 3:2
        "Wyndham Rewards":             1.0,   # added March 2026
    },
    "Wells Fargo Autograph Rewards": {
        # Airlines
        "Flying Blue (Air France/KLM)": 1.0,
        "British Airways Avios":       1.0,
        "Virgin Atlantic Flying Club": 1.0,
        "JetBlue TrueBlue":            1.0,
        "Iberia Avios":                1.0,
        # Hotels
        "Wyndham Rewards":             2.0,   # 1:2 — unusually good
        "Choice Privileges":           2.0,   # 1:2
    },
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
    via_transfer: bool = False,
    transfer_source: str = "",
    transfer_ratio: float = 1.0,
) -> PointsRedemption:
    cpp = cpp_table.get(program, cpp_table["default"])
    benchmark = cpp
    points_required = round(cash_value_usd / (cpp / 100))
    cash_value_of_balance = round(points_available * (cpp / 100), 2)
    can_cover_fully = points_available >= points_required
    can_cover_partially = (not can_cover_fully) and points_available >= points_required * 0.4

    recommendation = _redemption_quality(cpp, benchmark)
    if program_match:
        recommendation += " | Program match — best award availability"
    if via_transfer:
        recommendation += f" | Via transfer from {transfer_source} ({transfer_ratio:.2g}:1 ratio)"
    if can_cover_fully:
        recommendation += f" | Fully covered (${cash_value_usd:.0f} cash → {points_required:,} pts)"
    elif can_cover_partially:
        pts_short = points_required - points_available
        recommendation += f" | Partial — need {pts_short:,} more pts"

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
        via_transfer=via_transfer,
        transfer_source=transfer_source,
        transfer_ratio=transfer_ratio,
    )


def _credit_card_transfer_options(
    credit_cards: list[CreditCard],
    target_programs: list[str],
    cpp_table: dict[str, float],
    cash_value_usd: float,
) -> list[PointsRedemption]:
    """
    For each target loyalty program, check if any credit card can transfer enough
    points to cover the cost. Returns the best transfer options across all cards.
    """
    options: list[PointsRedemption] = []
    for card in credit_cards:
        if card.points_balance <= 0:
            continue
        partners = _CC_TRANSFER_PARTNERS.get(card.currency, {})
        for program in target_programs:
            ratio = partners.get(program)
            if ratio is None:
                continue
            # How many target-program points can this card generate?
            transferable_points = round(card.points_balance * ratio)
            if transferable_points <= 0:
                continue
            r = _calculate_redemption(
                program=program,
                cpp_table=cpp_table,
                points_available=transferable_points,
                cash_value_usd=cash_value_usd,
                program_match=False,
                via_transfer=True,
                transfer_source=card.currency,
                transfer_ratio=ratio,
            )
            options.append(r)
    # Best: full coverage first, then by CPP
    options.sort(key=lambda r: (not r.can_cover_fully, not r.can_cover_partially, -r.cpp))
    return options


def find_best_airline_redemptions(
    config: FamilyConfig, flight: FlightOption
) -> list[PointsRedemption]:
    results = []

    # Direct program points
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

    # Credit card transfer options — only surface if direct program can't fully cover
    direct_programs_with_balance = {a.loyalty_program for a in config.airlines if a.points_balance > 0}
    direct_can_cover = any(r.can_cover_fully for r in results)

    if not direct_can_cover and config.credit_cards:
        all_airline_programs = list(_AIRLINE_CPP.keys())
        transfer_opts = _credit_card_transfer_options(
            config.credit_cards, all_airline_programs, _AIRLINE_CPP, flight.price_usd
        )
        # Only include transfer options not already covered by direct programs
        for opt in transfer_opts:
            if opt.program_name not in direct_programs_with_balance:
                results.append(opt)

    results.sort(key=lambda r: (not r.program_match, r.via_transfer, not r.can_cover_fully, -r.cpp))
    return results


def find_best_hotel_redemptions(
    config: FamilyConfig, hotel: HotelOption
) -> list[PointsRedemption]:
    hotel_name_lower = hotel.name.lower()
    results = []

    # Direct hotel program points
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

    # Credit card transfer options — only surface if direct programs can't cover
    direct_can_cover = any(r.can_cover_fully for r in results)

    if not direct_can_cover and config.credit_cards:
        all_hotel_programs = list(_HOTEL_CPP.keys())
        transfer_opts = _credit_card_transfer_options(
            config.credit_cards, all_hotel_programs, _HOTEL_CPP, hotel.total_price_usd
        )
        direct_programs = {b.loyalty_program for b in config.hotel_brands}
        for opt in transfer_opts:
            if opt.program_name not in direct_programs:
                results.append(opt)

    results.sort(key=lambda r: (not r.program_match, r.via_transfer, not r.can_cover_fully, -r.cpp))
    return results


def get_best_cc_transfer(redemptions: list[PointsRedemption]) -> "PointsRedemption | None":
    """Returns the best credit card transfer option from a redemption list, if any."""
    transfers = [r for r in redemptions if r.via_transfer]
    return transfers[0] if transfers else None
