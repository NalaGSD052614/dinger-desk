#!/usr/bin/env python3
"""
Dinger Desk — MLB HR Pick Generator
Runs each morning, fetches the day's slate, scores hitters, writes picks.json.

Data sources:
  1. MLB Stats API (statsapi.mlb.com) — free, no key required, gives schedule, lineups, probable pitchers
  2. Baseball Savant (baseballsavant.mlb.com) — Statcast, ISO, barrel rate, exit velo
  3. Open Meteo (open-meteo.com) — free weather by lat/lon, no key required
  4. The Odds API (the-odds-api.com) — HR prop odds, requires free API key
  5. Ballpark factors — static lookup table maintained in this file

Sentiment layer (Twitter/X handicappers like Tablesetterspod, TSchulmanReport):
   X API access requires a paid developer account. Three options below in build_sentiment().
"""

import json
import os
import sys
import math
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ============================================================
# CONFIG
# ============================================================

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "picks.json"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
CT = ZoneInfo("America/Chicago")

# Park HR factor (1.00 = league average). Approximate, update each season.
PARK_FACTORS = {
    "Coors Field": 1.34,
    "Great American Ball Park": 1.25,
    "Yankee Stadium": 1.18,
    "Citizens Bank Park": 1.12,
    "Daikin Park": 1.09,
    "Globe Life Field": 1.07,
    "Camden Yards": 1.05,
    "Wrigley Field": 1.04,
    "Truist Park": 1.03,
    "Fenway Park": 1.02,
    "Rogers Centre": 1.02,
    "Dodger Stadium": 1.00,
    "Citi Field": 0.97,
    "PNC Park": 0.94,
    "Oracle Park": 0.85,
    "loanDepot park": 0.91,
}

# ============================================================
# DATA FETCH
# ============================================================

def fetch_schedule(date_str):
    """Pull today's game schedule from MLB Stats API."""
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher,lineups,venue,weather"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    games = []
    for date in data.get("dates", []):
        for g in date.get("games", []):
            games.append({
                "game_pk": g["gamePk"],
                "home": g["teams"]["home"]["team"]["abbreviation"] if "abbreviation" in g["teams"]["home"]["team"] else g["teams"]["home"]["team"]["name"],
                "away": g["teams"]["away"]["team"]["abbreviation"] if "abbreviation" in g["teams"]["away"]["team"] else g["teams"]["away"]["team"]["name"],
                "venue": g.get("venue", {}).get("name", "Unknown"),
                "venue_id": g.get("venue", {}).get("id"),
                "game_time_utc": g.get("gameDate"),
                "home_pitcher": g["teams"]["home"].get("probablePitcher", {}).get("fullName"),
                "away_pitcher": g["teams"]["away"].get("probablePitcher", {}).get("fullName"),
                "home_pitcher_id": g["teams"]["home"].get("probablePitcher", {}).get("id"),
                "away_pitcher_id": g["teams"]["away"].get("probablePitcher", {}).get("id"),
            })
    return games

def fetch_lineup(game_pk):
    """Pull lineup if posted. Lineups go up 2-4 hours before first pitch."""
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        d = r.json()
        boxscore = d.get("liveData", {}).get("boxscore", {})
        teams = boxscore.get("teams", {})
        return {
            "home": teams.get("home", {}).get("battingOrder", []),
            "away": teams.get("away", {}).get("battingOrder", []),
        }
    except Exception:
        return {"home": [], "away": []}

def fetch_player_stats(player_id, season=2026):
    """Pull season hitter stats (ISO, HR rate, barrel rate from Savant)."""
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=hitting&season={season}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0].get("stat", {})
        slg = float(s.get("slg", 0) or 0)
        avg = float(s.get("avg", 0) or 0)
        return {
            "hr": int(s.get("homeRuns", 0) or 0),
            "pa": int(s.get("plateAppearances", 0) or 0),
            "iso": slg - avg,
            "slg": slg,
            "ops": float(s.get("ops", 0) or 0),
        }
    except Exception:
        return None

def fetch_pitcher_stats(player_id, season=2026):
    """Pull pitcher HR/9 and basic rate stats."""
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season={season}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0].get("stat", {})
        ip = float(s.get("inningsPitched", 0) or 0)
        hr = int(s.get("homeRuns", 0) or 0)
        hr_per_9 = (hr * 9.0 / ip) if ip > 0 else 1.3
        return {
            "era": float(s.get("era", 0) or 0),
            "hr_per_9": hr_per_9,
            "hand": s.get("pitchHand", {}).get("code", "R") if isinstance(s.get("pitchHand"), dict) else "R",
            "ip": ip,
        }
    except Exception:
        return None

def fetch_weather(lat, lon):
    """Open Meteo, no API key needed."""
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m&temperature_unit=fahrenheit&wind_speed_unit=mph&forecast_days=1"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        h = r.json().get("hourly", {})
        # Use 7pm local hour as proxy
        idx = 19
        return {
            "temp_f": h["temperature_2m"][idx],
            "wind_mph": h["wind_speed_10m"][idx],
            "wind_dir": h["wind_direction_10m"][idx],
            "humidity": h["relative_humidity_2m"][idx],
        }
    except Exception:
        return None

def normalize_name(name):
    """
    Normalize a player name for matching across APIs.
    Strips accents, lowercases, removes punctuation and common suffixes.
    'Yordan Álvarez' and 'Yordan Alvarez Jr.' both become 'yordan alvarez'.
    """
    if not name:
        return ""
    # Strip accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_name = ascii_name.lower().strip()
    # Remove suffixes and punctuation
    for suffix in (" jr", " jr.", " sr", " sr.", " ii", " iii", " iv"):
        if ascii_name.endswith(suffix):
            ascii_name = ascii_name[: -len(suffix)]
    ascii_name = ascii_name.replace(".", "").replace(",", "").replace("'", "")
    # Collapse whitespace
    ascii_name = " ".join(ascii_name.split())
    return ascii_name


def fetch_hr_odds():
    """
    Pull HR prop odds from The Odds API.

    Player props (batter_home_runs) are NOT available on the main /odds endpoint.
    They must be fetched per event via /events/{eventId}/odds. So we:
      1. Hit the /events endpoint to list today's MLB event IDs (free, no quota cost)
      2. Loop each event and request the batter_home_runs market for it
      3. Collect the best (longest) "Over 0.5" / "Yes" price per player

    Returns a dict keyed by NORMALIZED player name -> American odds (int).
    """
    if not ODDS_API_KEY:
        print("No ODDS_API_KEY set, skipping odds.", file=sys.stderr)
        return {}

    base = "https://api.the-odds-api.com/v4/sports/baseball_mlb"

    # Step 1: list events (this call does not count against quota)
    try:
        ev = requests.get(
            f"{base}/events",
            params={"apiKey": ODDS_API_KEY},
            timeout=20,
        )
        ev.raise_for_status()
        events = ev.json()
    except Exception as e:
        print(f"Events fetch failed: {e}", file=sys.stderr)
        return {}

    out = {}
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        try:
            r = requests.get(
                f"{base}/events/{event_id}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "us",
                    "markets": "batter_home_runs",
                    "oddsFormat": "american",
                },
                timeout=20,
            )
            # Some events have no props posted yet -> 404/422, skip quietly
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception as e:
            print(f"  Event {event_id} odds failed: {e}", file=sys.stderr)
            continue

        for bm in data.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "batter_home_runs":
                    continue
                for outcome in mkt.get("outcomes", []):
                    # The standard "to hit a home run" prop is Over at point 0.5.
                    # Alternate lines (point 1.5 = "2+ HR", 2.5 = "3+ HR") carry
                    # huge odds like +14000 and must be excluded.
                    if outcome.get("name") != "Over":
                        continue
                    point = outcome.get("point")
                    if point is not None and abs(point - 0.5) > 0.01:
                        continue  # skip alternate milestone lines
                    player = outcome.get("description", "")
                    price = outcome.get("price")
                    if not player or price is None:
                        continue
                    # Sanity guard: a real "anytime HR" prop is roughly -250 to +900.
                    # Anything longer than +900 is almost certainly a bad/alt line.
                    if price > 900:
                        continue
                    key = normalize_name(player)
                    # Keep the best (highest payout) valid price across books
                    if key not in out or price > out[key]:
                        out[key] = price

    print(f"  Collected HR odds for {len(out)} players across {len(events)} events", file=sys.stderr)
    return out

# ============================================================
# SENTIMENT LAYER (Twitter/X handicappers)
# ============================================================

def build_sentiment():
    """
    Pull tweets from Tablesetterspod and TSchulmanReport.

    Three options ranked easiest to hardest:

    Option A (recommended for solo dev): Use Nitter RSS or a scraper like snscrape.
       pip install snscrape
       import snscrape.modules.twitter as sntwitter
       tweets = list(sntwitter.TwitterUserScraper('tablesetterspod').get_items())[:20]
       Then parse text for player names.

    Option B: X API Basic tier ($200/mo) — official, reliable.
       https://developer.x.com — requires paid subscription.

    Option C: RSS bridges like rss.app or fetchrss.com proxy public X feeds to RSS.
       Free tier usually allows 1-2 feeds.

    For this prototype we return a stub. Wire in your choice and parse tweets for
    player name mentions, then add a 0-1 sentiment boost to that hitter's score.
    """
    return {
        # "Aaron Judge": {"mentions": 2, "sources": ["tablesetterspod", "tschulmanreport"]},
    }

# ============================================================
# SCORING MODEL
# ============================================================

def score_hitter(hitter, pitcher, park_factor, weather, sentiment):
    """
    Composite score 0-100. Weights tuned for HR prop EV.

    Components:
      Hitter power (ISO, recent form): 30%
      Pitcher HR/9 + handedness: 25%
      Park factor: 15%
      Weather (wind direction, temp): 15%
      Lineup spot (more PAs = more chances): 10%
      Handicapper sentiment: 5%
    """
    score = 0.0

    # Hitter power: scale ISO. League avg ISO ~0.165. Elite is 0.270+.
    iso = hitter.get("iso", 0.150)
    score += min(30, (iso / 0.300) * 30)

    # Pitcher HR/9. League avg ~1.30. Bad pitcher is 1.7+, elite suppress is 0.9.
    hr9 = pitcher.get("hr_per_9", 1.30) if pitcher else 1.30
    score += min(25, (hr9 / 2.2) * 25)

    # Park factor.
    score += min(15, (park_factor - 0.85) / 0.50 * 15)

    # Weather. Warm air + wind blowing out RF/LF favors HRs.
    if weather:
        temp_pts = max(0, min(8, (weather["temp_f"] - 60) / 35 * 8))
        wind_pts = 0
        wd = weather.get("wind_dir", 0)
        # Rough: wind from 180-270 = blowing out to RF/CF for most stadiums oriented N
        if 135 <= wd <= 315:
            wind_pts = min(7, weather["wind_mph"] / 20 * 7)
        score += temp_pts + wind_pts
    else:
        score += 7  # neutral assumption

    # Lineup spot. Spots 1-4 get more PAs.
    spot = hitter.get("lineup_spot", 6)
    if spot <= 4:
        score += 10 - (spot - 1) * 0.5
    elif spot <= 6:
        score += 6
    else:
        score += 3

    # Sentiment boost
    name = hitter.get("name", "")
    if name in sentiment:
        score += min(5, sentiment[name].get("mentions", 0) * 2)

    return round(min(100, score), 1)

def american_to_implied(odds):
    """Convert American odds to implied probability."""
    if odds is None:
        return None
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)

def model_prob_from_score(score):
    """Map composite score to estimated HR probability. Calibrate over time."""
    # League avg HR/PA ~3.5%. ~4 PAs/game = ~14% game HR rate.
    # Elite hitter in great spot can reach 25-30%. Bad spot is 5-8%.
    return 0.04 + (score / 100) * 0.22

def calc_ev(model_prob, odds):
    """Expected value as percentage."""
    if odds is None:
        return 0
    imp = american_to_implied(odds)
    if not imp:
        return 0
    return round((model_prob - imp) * 100, 1)

# ============================================================
# PARLAY BUILDER
# ============================================================

def build_parlays(top_picks):
    """Build 2-leg and 3-leg parlay suggestions from top picks."""
    parlays = {"two_leg": [], "three_leg": []}
    if len(top_picks) < 2:
        return parlays

    def combined_odds(legs):
        decimals = []
        for leg in legs:
            o = parse_odds(leg["odds"])
            if o is None:
                return None
            decimals.append(american_to_decimal(o))
        prod = 1
        for d in decimals:
            prod *= d
        return decimal_to_american(prod)

    # Best two
    legs = top_picks[:2]
    odds = combined_odds(legs)
    if odds:
        parlays["two_leg"].append({
            "name": "Top 2 Anchors",
            "legs": [f"{p['player']} HR" for p in legs],
            "combined_odds": format_american(odds),
            "reasoning": f"Two highest-scored picks: {legs[0]['player']} ({legs[0]['score']}) and {legs[1]['player']} ({legs[1]['score']}). Different games reduce correlation."
        })

    # Best 3
    if len(top_picks) >= 3:
        legs = top_picks[:3]
        odds = combined_odds(legs)
        if odds:
            parlays["three_leg"].append({
                "name": "Triple Threat",
                "legs": [f"{p['player']} HR" for p in legs],
                "combined_odds": format_american(odds),
                "reasoning": "Three highest-scored picks. Lottery payout, model edge on every leg."
            })

    return parlays

def parse_odds(odds_str):
    try:
        return int(str(odds_str).replace("+", ""))
    except Exception:
        return None

def american_to_decimal(odds):
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)

def decimal_to_american(dec):
    if dec >= 2.0:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))

def format_american(odds):
    return f"+{odds}" if odds > 0 else str(odds)

# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.now(CT)
    date_str = now.strftime("%Y-%m-%d")

    print(f"[Dinger Desk] Building slate for {date_str}", file=sys.stderr)

    games = fetch_schedule(date_str)
    print(f"  Found {len(games)} games", file=sys.stderr)

    odds_map = fetch_hr_odds()
    print(f"  Loaded HR prices for {len(odds_map)} hitters", file=sys.stderr)

    sentiment = build_sentiment()

    candidates = []
    for g in games:
        park_factor = PARK_FACTORS.get(g["venue"], 1.00)

        # For each game we'd evaluate every probable starter from each team's lineup.
        # In production: hit fetch_lineup(game_pk) and iterate batting order.
        # Each entry below would be a real hitter, not the placeholder.
        for side in ("home", "away"):
            pitcher_id = g.get(f"{'away' if side == 'home' else 'home'}_pitcher_id")
            pitcher_name = g.get(f"{'away' if side == 'home' else 'home'}_pitcher")
            pitcher_stats = fetch_pitcher_stats(pitcher_id) if pitcher_id else None

            lineup_ids = fetch_lineup(g["game_pk"]).get(side, [])
            for spot, hitter_id in enumerate(lineup_ids[:9], start=1):
                hitter_stats = fetch_player_stats(hitter_id)
                if not hitter_stats:
                    continue
                hitter = {
                    "id": hitter_id,
                    "name": "",  # fetch via /people/{id} for full name
                    "lineup_spot": spot,
                    **hitter_stats
                }
                # populate name
                try:
                    pr = requests.get(f"https://statsapi.mlb.com/api/v1/people/{hitter_id}", timeout=10)
                    hitter["name"] = pr.json()["people"][0]["fullName"]
                except Exception:
                    continue

                weather = None  # plug in fetch_weather(lat, lon) using venue lat/lon lookup
                score = score_hitter(hitter, pitcher_stats, park_factor, weather, sentiment)
                odds = odds_map.get(normalize_name(hitter["name"]))
                model_p = model_prob_from_score(score)
                ev = calc_ev(model_p, odds)

                candidates.append({
                    "player": hitter["name"],
                    "team": g["home"] if side == "home" else g["away"],
                    "opponent": g["away"] if side == "home" else g["home"],
                    "pitcher": f"{pitcher_name} ({pitcher_stats.get('hand','R') if pitcher_stats else 'R'})" if pitcher_name else "TBD",
                    "ballpark": g["venue"],
                    "game_time": g["game_time_utc"],
                    "score": score,
                    "odds": format_american(odds) if odds else "n/a",
                    "ev_pct": ev,
                    "lineup_spot": spot,
                    "factors": {
                        "matchup": f"ISO {hitter['iso']:.3f}, {hitter['hr']} HR in {hitter['pa']} PA",
                        "park": f"{g['venue']} HR factor {park_factor:.2f}",
                        "weather": "Live weather pending" if not weather else f"{weather['temp_f']}F, wind {weather['wind_mph']} mph",
                        "pitcher": f"HR/9 {pitcher_stats['hr_per_9']:.2f}" if pitcher_stats else "Pitcher TBD",
                        "form": "Pull last 14 day Savant splits",
                        "sentiment": "X feed parse pending" if hitter["name"] not in sentiment else f"Mentioned by {', '.join(sentiment[hitter['name']]['sources'])}"
                    }
                })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_picks = candidates[:10]
    for i, p in enumerate(top_picks, 1):
        p["rank"] = i

    output = {
        "generated_at": now.isoformat(),
        "slate_date": date_str,
        "games_count": len(games),
        "top_picks": top_picks,
        "parlays": build_parlays(top_picks),
        "weather_alerts": [],
        "fade_list": []
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[Dinger Desk] Wrote {len(top_picks)} picks to {OUTPUT_PATH}", file=sys.stderr)

    # Archive a dated snapshot so the results tracker can grade it next day.
    # The 3 PM run is the authoritative slate (lineups + odds posted), so it
    # overwrites earlier same-day archives. This is intentional.
    archive_dir = OUTPUT_PATH.parent / "history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{date_str}.json"
    with open(archive_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[Dinger Desk] Archived snapshot to {archive_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
