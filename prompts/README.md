# Prompts

Reusable prompt templates for Goldenbird Capital research agents.

| File | Use |
|------|-----|
| `concall_forensic_summary.json` | Forensic earnings-call extraction (Gemini / local agents) |
| `realty_sector_master_alpha.json` | Master schema: peer table + alpha book |
| `realty_peer_table_DENSE.txt` | **Use this** — dense Q2 FY27 pack for Screener Realty mcap ≥ ₹500 cr + mgmt/forward/consensus |
| `realty_demand_pulse_TRY.txt` | **Demand pulse + sparklines** — 8-quarter history, booking/collections scores, breadth, city registrations |
| `realty_peer_table_TRY.txt` | Paste-sources-only (empty packs → all nulls) |
| `realty_peer_table_TRY_no_paste.txt` | Public-disclosure fill (can be thinner) |

**Realty dense flow**
1. Copy `realty_peer_table_DENSE.txt` into Gemini (optionally paste IR decks under symbols).
2. Save JSON → `data/realty_peer_compare.json`.
3. Hard-refresh local UI → **Realty** tab (detail rail shows mgmt summary/history/guidance, 1–2Q commentary, projections, consensus).

Universe snapshot: `data/realty_universe_screener.json` from [Screener Realty](https://www.screener.in/market/IN02/IN0205/IN020501/).

**Demand pulse flow**
1. Copy `realty_demand_pulse_TRY.txt` into Gemini (paste IR decks + city registration notes if you have them).
2. Save JSON → `data/realty_demand_pulse.json`.
3. UI graphs read `history` / `sparkline_ready` (pre-sales, collections, registrations).
