"""
Quick test of Travelpayouts Data API — run with your token:
    export TRAVELPAYOUTS_TOKEN=your_token_here
    python test_travelpayouts.py

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
"""
import os
import requests
from datetime import datetime, timedelta

TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
ORIGIN = "SFO"

if not TOKEN:
    print("ERROR: Set TRAVELPAYOUTS_TOKEN env var first")
    print("  export TRAVELPAYOUTS_TOKEN=your_token_here")
    exit(1)

def get_next_6_months():
    months = []
    d = datetime.now()
    for i in range(6):
        months.append((d + timedelta(days=30 * i)).strftime("%Y-%m"))
    return months

print(f"Searching for cheapest deals from {ORIGIN}...\n")
print("Testing two endpoints:\n")

# --- Endpoint 1: v1/prices/cheap (header auth, destination='-' = all routes) ---
print("=== v1/prices/cheap (header auth, all destinations) ===")
resp = requests.get(
    "https://api.travelpayouts.com/v1/prices/cheap",
    headers={"X-Access-Token": TOKEN},
    params={"origin": ORIGIN, "destination": "-", "currency": "usd", "limit": 5},
    timeout=15,
)
print(f"Status: {resp.status_code}")
if resp.ok:
    data = resp.json().get("data", {})
    print(f"Routes returned: {len(data)}")
    for iata, item in list(data.items())[:5]:
        print(f"  {iata}: ${item.get('price')} on {str(item.get('departure_at',''))[:10]} ({item.get('number_of_changes',0)} stops)")
else:
    print(f"Error: {resp.text[:200]}")

print()

# --- Endpoint 2: v3/get_cheap_prices (query param auth, per-month) ---
print("=== v3/get_cheap_prices per month (query param auth, top 5 per month) ===")
all_deals = []
for month in get_next_6_months():
    resp = requests.get(
        "https://api.travelpayouts.com/aviasales/v3/get_cheap_prices",
        params={
            "origin": ORIGIN,
            "departure_at": month,
            "unique": "false",
            "sorting": "price",
            "direct": "false",
            "currency": "usd",
            "limit": 5,
            "token": TOKEN,
        },
        timeout=15,
    )
    if resp.ok:
        data = resp.json()
        if not data.get("success"):
            print(f"  {month}: API error — {data}")
            continue
        items = data.get("data", {})
        if not items:
            print(f"  {month}: No deals found")
            continue
        for dest_iata, item in list(items.items())[:5]:
            price = item.get("price", 0)
            dep = str(item.get("departure_at", ""))[:10]
            stops = item.get("number_of_changes", 0)
            all_deals.append((price, dest_iata, dep, month))
            print(f"  {month}: ${price:4} to {dest_iata} on {dep} ({stops} stops)")
    else:
        print(f"  {month}: HTTP {resp.status_code} — {resp.text[:150]}")

print(f"\n=== Top 10 cheapest across all months ===")
all_deals.sort()
for price, dest, dep, month in all_deals[:10]:
    print(f"  ${price:4} to {dest} departing {dep}")
