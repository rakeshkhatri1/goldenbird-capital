# Goldenbird Capital

Personal India markets research desk: Nifty chart, FII/DII flows, broker research tracker, deep-research pipeline, concall scans, and stage-based alerts.

**Repo:** [rakeshkhatri1/goldenbird-capital](https://github.com/rakeshkhatri1/goldenbird-capital)  
**UI:** open `index.html` via a local static server (see below).

---

## What’s in the product

| Area | What it does |
|------|----------------|
| **Nifty home** | TradingView Nifty chart + FII/DII panel + Motilal/peer research feed |
| **Screener** | Technical universe from `nse_market_data.json` (local) |
| **Sectors / Compare** | Sector tags + peer compare |
| **Updates** | NSE business filings (`data/business_updates.json`) + optional Gemini PDF summarize |
| **Research** | ₹100–1,000 Cr governance band, track/review/skip, Keep/Pass |
| **Pipeline** | Stages: Idea → Watch → Diligence → Ready → Position → Exit |
| **Alerts** | Diff-based alerts on filings, verdict/grade, technicals, ±5% moves (browser + optional Notification API) |
| **Concall Scans** | StockScans-style table from Screener concall index (period, transcript, count, track) |

---

## Quick start (UI only)

```bash
cd goldenbird-capital
python3 -m http.server 8081
```

Open [http://127.0.0.1:8081](http://127.0.0.1:8081).

Optional Gemini proxy (for Updates summarize / rank tools):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# put GEMINI_API_KEY in .env (never commit .env)
python3 gemini_server.py   # http://127.0.0.1:8787
```

---

## Data (not in git)

`data/`, `nse_market_data.json`, `.env`, and caches are **gitignored** on purpose. Build them locally:

| Script | Output | Purpose |
|--------|--------|---------|
| `build_fii_dii.py` | `data/fii_dii.json` | NSE FII/DII cash flows |
| `build_research_articles.py` | `data/research_articles.json` | Motilal sheet + Moneycontrol notes |
| `build_business_updates.py` | `data/business_updates.json` | Corporate filings |
| `build_technicals.py` | `nse_market_data.json` | Screener technicals |
| `build_governance.py` / `enrich_governance.py` | `data/governance.json` | Deep research scores |
| `enrich_concalls.py` | `data/concalls.json` | Concall index from Screener |
| `build_valuation.py` | `data/valuation.json` | Valuation / growth |
| `build_sector_tags.py` | `data/sector_tags.json` | Sector taxonomy |
| `build_market_cap.py` | `data/market_cap.json` | Universe mcap filter |

Example:

```bash
source .venv/bin/activate
python3 build_fii_dii.py
python3 build_research_articles.py
python3 enrich_concalls.py
```

---

## Pipeline & alerts

1. **Research** → Keep a stock  
2. Set **Pipeline stage** + alert prefs (filings / verdict / technicals / ±5%)  
3. Open **Pipeline** for the board view  
4. Header **Alerts** shows new events after you refresh data and reload the page  

First load after adding a name only baselines snapshots; alerts appear on later refreshes.

---

## Concall highlights (current vs next)

**Now:** metadata scan + Screener docs link (not full AI notes).  

**Next options:** on-demand Gemini summarize (like Updates), batch `build_concall_highlights.py` for Keep/pipeline names, or BSE/NSE transcript attachments.

---

## Repo layout

```
index.html                 # main Goldenbird UI
build_*.py / enrich_*.py   # data builders
gemini_server.py           # local Gemini proxy
data/                      # local only (gitignored)
zepstream/                 # separate Vite prototype
```

---

## Security notes

- Never commit `.env` or API / GitHub tokens  
- Motilal CloudFront PDF URLs are often gated (403); UI uses hub / Moneycontrol links where possible  
- Keep the GitHub repo private if it stays personal research tooling  

---

## License

Private personal project unless you add an explicit license.
