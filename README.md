# Sun Family Weekly Travel Planner

Automated weekly email with the top 3 personalized travel plans for the Sun family. Runs every Monday via GitHub Actions.

## What It Does

1. Reads family preferences from `family_preferences.yaml`
2. Scans the next 6 months for travel opportunities matching your availability pattern
3. **AI (DeepSeek R1 via OpenRouter)** suggests 4 candidate destinations with optimal travel dates
4. For each destination, gathers real data:
   - Flights via **Amadeus** (or Kiwi/Tequila as Tier 2)
   - Hotels via **Amadeus** (or Travelpayouts as Tier 2)
   - Weather via **OpenWeatherMap**
   - Award seat availability via **Award Flight Daily** (or Seats.aero as Tier 2)
5. Calculates loyalty points redemption value using industry CPP benchmarks
6. **AI** synthesizes everything into ranked top 3 plans with pros/cons
7. Sends a formatted HTML email via **Gmail SMTP**

---

## Setup

### Step 1 — Get API Keys (Tier 1, all free)

| Service | How to get the key | Secret name |
|---|---|---|
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) → Sign up, no credit card | `OPENROUTER_API_KEY` |
| **Amadeus** | [developers.amadeus.com](https://developers.amadeus.com) → Self-Service → Create app | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` |
| **OpenWeatherMap** | [openweathermap.org](https://openweathermap.org/api) → Free account | `OPENWEATHER_API_KEY` |
| **Gmail App Password** | Google Account → Security → 2-Step Verification → App Passwords → Create | `GMAIL_EMAIL`, `GMAIL_APP_PASSWORD` |

### Step 2 — Add Secrets to GitHub

Go to: **Repository → Settings → Secrets and variables → Actions → New repository secret**

Add these secrets (at minimum):
- `OPENROUTER_API_KEY`
- `AMADEUS_CLIENT_ID`
- `AMADEUS_CLIENT_SECRET`
- `OPENWEATHER_API_KEY`
- `GMAIL_EMAIL`
- `GMAIL_APP_PASSWORD`

### Step 3 — Update `family_preferences.yaml`

Edit the file to reflect your actual:
- Points balances for each loyalty program
- Budget and trip duration preferences
- Blackout dates
- Home airport

### Step 4 — Test Manually

Go to **Actions → Weekly Travel Planner → Run workflow** to trigger a test run without waiting for Monday.

---

## Upgrading API Tiers

Set these GitHub Secrets to switch providers (no code changes needed):

### Better flight coverage (Kiwi/Tequila)
Register at [tequila.kiwi.com](https://tequila.kiwi.com), then add:
- Secret: `KIWI_API_KEY` = your key
- Secret: `FLIGHT_PROVIDER` = `kiwi`

### More award availability data (Seats.aero — $9.99/month)
Sign up at [seats.aero](https://seats.aero), then add:
- Secret: `SEATS_AERO_API_KEY` = your key
- Secret: `AWARD_PROVIDER` = `seats_aero`

### Real-time points balance sync (AwardWallet — optional)
No loyalty program or credit card issuer offers a public balance API. **AwardWallet** (awardwallet.com) is the only practical solution — it aggregates 600+ programs and has a developer API.

**Setup:**
1. Sign up at awardwallet.com (free, or $49.99/year for Plus which enables API access)
2. Connect your airline, hotel, AND credit card accounts inside AwardWallet
3. Go to awardwallet.com/api → generate a **Personal API Token**
4. Add it as GitHub Secret: `AWARDWALLET_TOKEN`

Once set, every Monday run will call AwardWallet first, update all balances (airlines, hotels, credit cards) to live values, then proceed with planning. Without the token, balances from `family_preferences.yaml` are used.

### Credit card flexible points (Chase UR, Amex MR, etc.)
These are already supported — no extra setup needed beyond adding your cards to `family_preferences.yaml`:

```yaml
credit_cards:
  - name: "Chase Sapphire Reserve"
    currency: "Chase Ultimate Rewards"
    points_balance: 75000
  - name: "Amex Platinum"
    currency: "Amex Membership Rewards"
    points_balance: 40000
  - name: "Capital One Venture X"
    currency: "Capital One Venture Miles"
    points_balance: 25000
  - name: "Bilt Mastercard"
    currency: "Bilt Points"
    points_balance: 15000
  - name: "Citi Strata Premier"
    currency: "Citi ThankYou Points"
    points_balance: 30000
```

All transfer partners and ratios are built into the code. When your direct loyalty points can't fully cover a trip, the email will show a highlighted "Transfer option" row indicating which credit card to transfer from and to which program.

**Supported currencies:** Chase Ultimate Rewards, Amex Membership Rewards, Capital One Venture Miles, Citi ThankYou Points, Bilt Points, Wells Fargo Autograph Rewards

### Different AI model
The default is `deepseek/deepseek-r1:free` (free, excellent reasoning). To use a different model:
- Secret: `AI_MODEL` = any [OpenRouter model ID](https://openrouter.ai/models)
- Example: `meta-llama/llama-3.3-70b-instruct:free` (also free, faster)

---

## Loyalty Points Methodology

Points values use industry-standard CPP (cents per point) benchmarks from The Points Guy / NerdWallet (last reviewed April 2026). These are estimates — actual redemption value depends on route, availability, and program rules.

| Program | CPP Benchmark |
|---|---|
| Alaska Mileage Plan | 1.80c |
| American AAdvantage | 1.77c |
| World of Hyatt | 1.70c |
| Southwest Rapid Rewards | 1.50c |
| United MileagePlus | 1.35c |
| JetBlue TrueBlue | 1.30c |
| Delta SkyMiles | 1.20c |
| Marriott Bonvoy | 0.84c |
| Wyndham Rewards | 0.90c |
| Hilton Honors | 0.60c |

Update these annually in `src/loyalty_calculator.py`.

---

## Project Structure

```
travel_plan_suns_family/
├── family_preferences.yaml       # Your family config — update this
├── requirements.txt
├── .github/workflows/
│   └── weekly_travel_planner.yml
├── src/
│   ├── main.py                   # Orchestrator
│   ├── models.py                 # Shared dataclasses
│   ├── config_loader.py          # Config parsing + 6-month window generation
│   ├── ai_planner.py             # AI destination suggestion + synthesis
│   ├── flights.py                # Pluggable: Amadeus -> Kiwi
│   ├── hotels.py                 # Pluggable: Amadeus -> Travelpayouts
│   ├── weather.py                # OpenWeatherMap
│   ├── award_search.py           # Pluggable: Award Flight Daily -> Seats.aero
│   ├── loyalty_calculator.py     # CPP-based points value math
│   └── email_sender.py           # Jinja2 HTML + Gmail SMTP
└── templates/
    └── email_template.html       # Responsive HTML email template
```
