# Sun Family Weekly Travel Planner

Automated weekly email with the top 5 personalized travel plans for the Sun family. Runs every Monday via GitHub Actions.

## What It Does

1. Reads family preferences from `family_preferences.yaml`
2. Scans the next 6 months for travel opportunities
3. **AI (DeepSeek R1 via OpenRouter)** suggests 4 candidate destinations with optimal dates
4. For each destination, gathers real data:
   - Flights via **SerpAPI** (primary), **Amadeus** (free real-time), or **Travelpayouts** (cached)
   - Hotels via **SerpAPI Google Hotels** or **Travelpayouts Hotellook**
   - Weather via **OpenWeatherMap**
   - Award seat availability via **Award Flight Daily** (or Seats.aero)
5. Calculates loyalty points + credit card transfer redemption value
6. **AI** synthesizes everything into ranked top 5 plans with pros/cons
7. Sends a formatted HTML email via **Gmail SMTP**

---

## Flight Provider Priority

The system cascades through providers in order of data quality:

| Priority | Provider | Data Type | Cost | Setup |
|---|---|---|---|---|
| 1st | **SerpAPI** | Google Flights scraping | Free 250/mo | [serpapi.com](https://serpapi.com) |
| 2nd | **Amadeus** | Real-time GDS data | Free tier | [developers.amadeus.com](https://developers.amadeus.com) |
| 3rd | **Travelpayouts** | Cached Aviasales data | Free affiliate | [travelpayouts.com](https://travelpayouts.com) |
| 4th | **Kiwi/Tequila** | Live aggregator | Free partner | [tequila.kiwi.com](https://tequila.kiwi.com) |

> **Note on Travelpayouts**: This API returns cached data from recent user searches on Aviasales (primarily Russian users). US-originating routes like SFO often have sparse data. We send `market=us` to improve results, but Amadeus or SerpAPI are recommended for reliable US route coverage.

> **Note on Amadeus**: The Self-Service portal is scheduled for decommission on July 17, 2026. It works well until then with a generous free tier.

---

## Setup — What You Need to Do

### Step 1 — Get API keys (pick at least one flight provider)

**A. OpenRouter** (AI — free, ~2 min) ⭐ Required
1. Go to openrouter.ai → Sign Up
2. Dashboard → Keys → Create Key
3. Copy the key

**B. SerpAPI** (flights + hotels — best data, ~3 min) ⭐ Recommended
1. Go to serpapi.com → Sign Up
2. Dashboard → API Key → copy it
3. Free tier: 250 searches/month (enough for weekly runs)

**C. Amadeus** (real-time flight data — free, ~5 min)
1. Go to developers.amadeus.com → Register
2. Dashboard → My Self-Service Workspace → Create New App
3. Copy your **API Key** and **API Secret**
4. Starts in test mode; set `AMADEUS_ENV=production` for live data

**D. Travelpayouts** (flights + hotels — free affiliate, ~5 min)
1. Go to travelpayouts.com → Sign Up
2. Programs → find "Aviasales" or "Booking.com" → Join
3. Your affiliate token appears in your dashboard under "API" or "Tools"
4. This single token is used for **both** flight search and hotel search

**E. OpenWeatherMap** (weather — free, ~3 min) ⭐ Required
1. Go to openweathermap.org → Sign Up
2. API Keys tab → copy the default key (activates in ~2 hours)

**F. Gmail App Password** (email delivery — ~5 min) ⭐ Required
1. Your Google Account must have 2-Step Verification turned on
2. Go to myaccount.google.com → Security → 2-Step Verification → App Passwords
3. Create one named "Travel Planner" → copy the 16-character password

---

### Step 2 — Add secrets to GitHub

Go to: **Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value | Required? |
|---|---|---|
| `OPENROUTER_API_KEY` | from openrouter.ai | ✅ Yes |
| `OPENWEATHER_API_KEY` | from openweathermap.org | ✅ Yes |
| `GMAIL_EMAIL` | your full Gmail address | ✅ Yes |
| `GMAIL_APP_PASSWORD` | the 16-char app password (no spaces) | ✅ Yes |
| `SERPAPI_API_KEY` | from serpapi.com | Recommended |
| `AMADEUS_API_KEY` | from developers.amadeus.com | Optional |
| `AMADEUS_API_SECRET` | from developers.amadeus.com | Optional |
| `AMADEUS_ENV` | `test` (default) or `production` | Optional |
| `TRAVELPAYOUTS_TOKEN` | from travelpayouts.com | Optional |
| `KIWI_API_KEY` | from tequila.kiwi.com | Optional |

---

### Step 3 — Test it

Go to **Actions → Weekly Travel Planner → Run workflow → Run workflow**

Watch the logs (~2 min). You'll receive the email or see a clear error in the logs.

---

## Optional upgrades (add later)

| What | Benefit | How |
|---|---|---|
| **AwardWallet** ($49.99/yr) | Live balance sync every Monday — no more manual YAML updates | Sign up → connect accounts → add `AWARDWALLET_TOKEN` secret |
| **Seats.aero** ($9.99/mo) | Better award seat data | Add `SEATS_AERO_API_KEY` + `AWARD_PROVIDER=seats_aero` secrets |

---

## Credit card points (already set up)

Your Amex Membership Rewards (200k) and Bilt Points (150k) are already in the YAML. The system automatically checks which credit card transfers can cover a flight or hotel when your direct loyalty points fall short, and shows a transfer suggestion in the email.

**Supported currencies:** Chase Ultimate Rewards, Amex Membership Rewards, Capital One Venture Miles, Citi ThankYou Points, Bilt Points, Wells Fargo Autograph Rewards

To add more cards, edit `family_preferences.yaml`:
```yaml
credit_cards:
  - name: "Chase Sapphire Reserve"
    currency: "Chase Ultimate Rewards"
    points_balance: 75000
```

---

## AwardWallet live balance sync (optional)

No loyalty program or credit card issuer offers a public balance API. AwardWallet is the only practical solution — it aggregates 600+ programs and has a developer API.

1. Sign up at awardwallet.com (Plus plan: $49.99/year — needed for API access)
2. Connect all your airline, hotel, and credit card accounts inside AwardWallet
3. Go to awardwallet.com/api → generate a Personal API Token
4. Add GitHub Secret: `AWARDWALLET_TOKEN`

Without this, update balances manually in `family_preferences.yaml` whenever they change.

---

## Loyalty Points CPP Benchmarks

Values from The Points Guy / NerdWallet, April 2026. Update annually in `src/loyalty_calculator.py`.

| Program | CPP |
|---|---|
| Alaska Mileage Plan | 1.80c |
| American AAdvantage | 1.77c |
| World of Hyatt | 1.70c |
| Air Canada Aeroplan | 1.55c |
| Southwest Rapid Rewards | 1.50c |
| United MileagePlus | 1.35c |
| JetBlue TrueBlue | 1.30c |
| Delta SkyMiles | 1.20c |
| Marriott Bonvoy | 0.84c |
| Wyndham Rewards | 0.90c |
| Hilton Honors | 0.60c |

---

## Project Structure

```
travel_plan_suns_family/
├── family_preferences.yaml       # Your family config — edit this
├── requirements.txt
├── test_travelpayouts.py         # Quick API test script
├── .github/workflows/
│   └── weekly_travel_planner.yml
├── src/
│   ├── main.py                   # Orchestrator
│   ├── models.py                 # Shared dataclasses
│   ├── config_loader.py          # Config parsing + window generation
│   ├── ai_planner.py             # AI destination suggestion + synthesis
│   ├── amadeus_auth.py           # Amadeus OAuth2 token management
│   ├── balance_sync.py           # AwardWallet live balance sync
│   ├── flights.py                # SerpAPI → Amadeus → Travelpayouts → Kiwi
│   ├── hotels.py                 # SerpAPI Hotels → Travelpayouts Hotellook
│   ├── weather.py                # OpenWeatherMap
│   ├── award_search.py           # Award Flight Daily + Seats.aero
│   ├── loyalty_calculator.py     # CPP math + credit card transfer logic
│   └── email_sender.py           # Jinja2 HTML + Gmail SMTP
└── templates/
    └── email_template.html
```
