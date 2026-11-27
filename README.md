# Goldenbird Capital

Personal India markets research desk: Nifty chart, FII/DII flows, broker research tracker, deep-research pipeline, concall scans, and stage-based alerts.

**Repo:** [rakeshkhatri1/goldenbird-capital](https://github.com/rakeshkhatri1/goldenbird-capital)  
**UI (local):** open `index.html` via a static server (see below).  
**UI (hosted):** GitHub Pages deploys on every push to `main` (Actions → Deploy GitHub Pages).

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

### Python package (builders / R2)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # library + pytest/ruff
# pip install -e ".[dev,market]"  # + kite/numpy/pandas for market scripts
cp .env.example .env             # fill secrets locally — never commit .env
pytest
```

Optional Gemini proxy (Updates summarize / rank tools):

```bash
python3 gemini_server.py   # http://127.0.0.1:8787
```

### Hosted site (GitHub Pages)

1. Repo **Settings → Pages → Source: GitHub Actions**
2. Push to `main` (workflow `.github/workflows/pages.yml`)
3. Site URL: `https://<user>.github.io/goldenbird-capital/` (or the URL shown in the Actions deploy job)

The Pages artifact includes `index.html`, `config.js`, curated `data/` from `site/data/`, and `examples/`. Heavy market dumps stay out of git.

To point the UI at Cloudflare R2 later, set public `ui/` URL in `config.js`:

```js
window.GOLDENBIRD_CONFIG = { dataBaseUrl: "https://<public-r2>/ui", geminiApiBase: "" };
```

Upload packs:

```bash
python -m goldenbird.cli.upload_ui --dry-run
python -m goldenbird.cli.upload_ui
```

---

## Data (not in git)

Working copies under `data/`, `nse_market_data.json`, `.env`, and caches are **gitignored**. Curated demo packs for Pages live in **`site/data/`** (committed). Build locally into `data/` as needed:

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
index.html                 # main Goldenbird UI (legacy monolith; config via config.js)
config.js                  # runtime UI config (no secrets)
site/                      # Pages inputs (config + curated data/)
src/goldenbird/            # scalable Python package (config, storage, CLI)
tests/                     # pytest
.github/workflows/         # CI + Pages
build_*.py / enrich_*.py   # legacy builders (migrate into src/ over time)
gemini_server.py           # local Gemini proxy
data/                      # local only (gitignored)
examples/                  # sample JSON schemas
prompts/                   # Gemini / research prompts
zepstream/                 # separate Vite prototype
```

Target direction: new code lands in `src/goldenbird/` with tests; root `build_*.py` scripts shrink into thin CLIs. UI stays static + JSON packs (R2 `ui/` or `site/data/`).

---

## Security notes

- Never commit `.env` or API / GitHub tokens (use GitHub Actions **Secrets** for CI uploads)  
- Motilal CloudFront PDF URLs are often gated (403); UI uses hub / Moneycontrol links where possible  
- Keep the GitHub repo private if it stays personal research tooling  
- Browser must never receive R2 secret keys — only a public `dataBaseUrl` if used  

---

## License

Private personal project unless you add an explicit license.
