# Sun Family Weekly Travel Planner

Automated weekly email with the top 3 personalized travel plans for the Sun family. Runs every Monday via GitHub Actions.

## What It Does

1. Reads family preferences from `family_preferences.yaml`
2. Scans the next 6 months for travel opportunities
3. **AI (DeepSeek R1 via OpenRouter)** suggests 4 candidate destinations with optimal dates
4. For each destination, gathers real data:
   - Flights via **Kiwi/Tequila** (nonstop preferred)
   - Hotels via **Travelpayouts**
   - Weather via **OpenWeatherMap**
   - Award seat availability via **Award Flight Daily** (or Seats.aero)
5. Calculates loyalty points + credit card transfer redemption value
6. **AI** synthesizes everything into ranked top 3 plans with pros/cons
7. Sends a formatted HTML email via **Gmail SMTP**

---

## Setup — What You Need to Do

### Step 1 — Get these 5 API keys

**A. OpenRouter** (AI — free, ~2 min)
1. Go to openrouter.ai → Sign Up
2. Dashboard → Keys → Create Key
3. Copy the key

**B. Kiwi/Tequila** (flights — free sandbox, ~5 min)
1. Go to tequila.kiwi.com → Register
2. Go to My Apps → Create App → copy the API key
3. The sandbox key works immediately for testing
4. For production use (after testing works): email partners@kiwi.com with a brief note about your use case

**C. Travelpayouts** (hotels — free affiliate, ~5 min)
1. Go to travelpayouts.com → Sign Up
2. Programs → Find "Hotels.com" or "Booking.com" → Join
3. Your affiliate token appears in your dashboard under "API" or "Tools"

**D. OpenWeatherMap** (weather — free, ~3 min)
1. Go to openweathermap.org → Sign Up
2. API Keys tab → copy the default key (activates in ~2 hours)

**E. Gmail App Password** (email delivery — ~5 min)
1. Your Google Account must have 2-Step Verification turned on
2. Go to myaccount.google.com → Security → 2-Step Verification → App Passwords
3. Create one named "Travel Planner" → copy the 16-character password

---

### Step 2 — Add secrets to GitHub

Go to: **Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `OPENROUTER_API_KEY` | from openrouter.ai |
| `KIWI_API_KEY` | from tequila.kiwi.com |
| `TRAVELPAYOUTS_TOKEN` | from travelpayouts.com |
| `OPENWEATHER_API_KEY` | from openweathermap.org |
| `GMAIL_EMAIL` | your full Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-char app password (no spaces) |

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
├── .github/workflows/
│   └── weekly_travel_planner.yml
├── src/
│   ├── main.py                   # Orchestrator
│   ├── models.py                 # Shared dataclasses
│   ├── config_loader.py          # Config parsing + window generation
│   ├── ai_planner.py             # AI destination suggestion + synthesis
│   ├── balance_sync.py           # AwardWallet live balance sync
│   ├── flights.py                # Kiwi/Tequila (default) + Amadeus fallback
│   ├── hotels.py                 # Travelpayouts (default) + Amadeus fallback
│   ├── weather.py                # OpenWeatherMap
│   ├── award_search.py           # Award Flight Daily + Seats.aero
│   ├── loyalty_calculator.py     # CPP math + credit card transfer logic
│   └── email_sender.py           # Jinja2 HTML + Gmail SMTP
└── templates/
    └── email_template.html
```
