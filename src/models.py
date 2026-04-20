from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AirlineLoyalty:
    name: str
    iata_code: str
    loyalty_program: str
    points_balance: int


@dataclass
class HotelBrand:
    name: str
    loyalty_program: str
    points_balance: int


@dataclass
class AvailabilityConfig:
    pattern: str  # "school_holidays_and_weekends", "anytime", "weekends_only"
    exclude_dates: list[str]
    lookahead_months: int
    book_at_least_weeks_ahead: int


@dataclass
class FamilyConfig:
    home_airport: str
    adults: int
    children_ages: list[int]
    trip_duration_min: int
    trip_duration_max: int
    budget_usd: float
    max_flight_hours: float
    availability: AvailabilityConfig
    destination_interests: list[str]
    visa_free_only: bool
    passport_countries: list[str]
    exclude_regions: list[str]
    hotel_star_min: int
    hotel_brands: list[HotelBrand]
    airlines: list[AirlineLoyalty]
    notification_email: str


@dataclass
class FlightOption:
    origin: str
    destination: str
    departure_date: str
    return_date: str
    airline: str
    airline_iata: str
    price_usd: float
    duration_hours: float
    stops: int
    provider: str  # "amadeus" or "kiwi"


@dataclass
class HotelOption:
    name: str
    brand: str
    star_rating: float
    price_per_night_usd: float
    total_price_usd: float
    nights: int
    location: str
    provider: str  # "amadeus", "travelpayouts", or "xotelo"


@dataclass
class WeatherSummary:
    destination: str
    travel_date_range: str
    avg_temp_celsius: float
    conditions: str
    precipitation_chance_pct: float
    is_forecast: bool  # False means we only have historical/seasonal estimate


@dataclass
class AwardAvailability:
    program: str
    origin: str
    destination: str
    travel_date: str
    seats_available: int
    cabin: str  # "economy", "business", "first"
    points_required: int
    provider: str  # "award_flight_daily" or "seats_aero"


@dataclass
class PointsRedemption:
    program_name: str
    points_available: int
    points_required: int
    cash_value_usd: float
    cpp: float  # cents per point
    cpp_benchmark: float
    can_cover_fully: bool
    can_cover_partially: bool
    recommendation: str
    program_match: bool  # True if the flight/hotel brand matches this program


@dataclass
class DestinationPlan:
    destination_city: str
    destination_iata: str
    country: str
    suggested_rationale: str
    flight: Optional[FlightOption] = None
    hotel: Optional[HotelOption] = None
    weather: Optional[WeatherSummary] = None
    award_availability: list[AwardAvailability] = field(default_factory=list)
    flight_points_options: list[PointsRedemption] = field(default_factory=list)
    hotel_points_options: list[PointsRedemption] = field(default_factory=list)
    total_cash_cost_usd: float = 0.0
    data_errors: list[str] = field(default_factory=list)


@dataclass
class TravelPlan:
    rank: int
    destination_plan: DestinationPlan
    ai_recommendation_text: str
    pros: list[str]
    cons: list[str]
    best_for: str
    overall_score: float
