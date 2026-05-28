# Dinger Desk · MLB Home Run Agent

Mobile-friendly dashboard that generates daily MLB home run prop picks before first pitch. Hosted on GitHub Pages. Auto-refreshes each morning via GitHub Actions.

## What it does

Each morning at 9:00 AM Central, the agent:

1. Pulls the day's MLB slate (schedule, probable pitchers, lineups when posted) from the free MLB Stats API
2. Pulls hitter ISO and pitcher HR/9 from MLB Stats API
3. Pulls live HR prop odds from The Odds API
4. Pulls weather from Open Meteo (free, no key)
5. Applies a 5 factor composite score: matchup, park, weather, pitcher, form, plus an optional handicapper sentiment layer
6. Writes `data/picks.json` with the ranked top picks and parlay suggestions
7. Commits the file so GitHub Pages serves the fresh dashboard

## Setup (one time)

### 1. Create the repo
Create a new public GitHub repo (e.g. `dinger-desk`) and push every file in this folder to it.

### 2. Get an Odds API key
Go to https://the-odds-api.com and create a free account. The free tier covers about 500 requests per month, which is plenty for one daily run.

### 3. Add the key to GitHub Secrets
In your repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
Name: `ODDS_API_KEY`
Value: your key.

### 4. Enable GitHub Pages
**Settings → Pages → Source → Deploy from a branch → main → / (root) → Save**.
Your dashboard goes live at `https://<your-username>.github.io/dinger-desk/`.

### 5. Verify the workflow
**Actions tab → Generate Daily Picks → Run workflow**. This triggers a manual run and confirms everything works. The workflow runs automatically every morning at 14:00 UTC (9 AM CDT).

## File structure

```
dinger-desk/
├── index.html              # the dashboard (mobile responsive)
├── data/
│   └── picks.json          # generated each morning
├── scripts/
│   └── generate_picks.py   # the pick generator
└── .github/
    └── workflows/
        └── daily-picks.yml # cron + script runner
```

## The scoring model

Composite score 0-100, weighted as:

| Factor | Weight | Source |
|--------|--------|--------|
| Hitter power (ISO, season HR) | 30% | MLB Stats API |
| Pitcher HR/9 + handedness | 25% | MLB Stats API |
| Park factor | 15% | Static lookup in script |
| Weather (temp, wind direction) | 15% | Open Meteo |
| Lineup spot | 10% | MLB Stats API (when lineup posted) |
| Handicapper sentiment | 5% | X feed parse (optional, see below) |

Picks above 85 = Tier 1 (highest confidence, red border).
Picks 78 to 85 = Tier 2 (solid play, gold border).
Picks below 78 = Tier 3 (leverage/contrarian, gray border).

EV % is calculated against the implied probability from the market price. Positive EV means the model believes the price is too long.

## Adding the X / Twitter handicapper layer

The free MLB Stats API has no rate limit headache, but Twitter/X requires either money or scraping. Three options inside `generate_picks.py` in `build_sentiment()`:

**Option A (recommended): snscrape.** Free Python library that scrapes public X profiles without an API key. Install with `pip install snscrape`. May break if X changes their site; check the snscrape GitHub for status.

**Option B: X API Basic tier.** $200/month, official, never breaks. Worth it if this matters.

**Option C: RSS bridges** like rss.app or fetchrss.com that proxy public X feeds to RSS. Free tiers usually allow 1-2 feeds. Then parse the RSS each morning for player name mentions.

Once you have tweets, search each tweet text for player surnames from your candidate pool. Build a dict like `{"Aaron Judge": {"mentions": 2, "sources": ["tablesetterspod", "tschulmanreport"]}}` and return it from `build_sentiment()`. The score function adds up to 5 points per player based on mention count.

## Improvements to add later

* **Pull batter vs pitcher (BvP) history.** MLB Stats API supports it via `?stats=vsPlayer&opposingPlayerId=X`. Bake into the matchup factor.
* **Use Statcast barrel rate and 95+ mph exit velocity rate.** Pull from Baseball Savant CSV exports. These predict HRs better than ISO alone.
* **Pull pitcher splits vs handedness.** A righty pitcher's HR/9 vs lefties is what matters when the hitter is left handed.
* **Track results.** Write a `scripts/log_results.py` that runs the morning after, hits the MLB API for yesterday's box scores, marks each pick win/loss, and appends to a `results.csv`. This becomes your ROI tracker.
* **Add a results page** to the dashboard. New tab in `index.html` that reads `results.csv` and shows hit rate, ROI, average odds.

## Disclaimer

This is an educational projection system. It is not financial advice. Bet within your means. If gambling becomes a problem, call 1-800-GAMBLER.
