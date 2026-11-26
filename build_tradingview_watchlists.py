"""
Build TradingView paste-ready watchlist files from data/tradingview_watchlists.json.

Resolves NSE/BSE prefix from screener_company_map.json (same logic as index.html).

Usage:
  python3 build_tradingview_watchlists.py
  TV_OUT_DIR=data/tradingview python3 build_tradingview_watchlists.py

Output per watchlist:
  data/tradingview/{id}.txt          — one symbol per line (NSE:SYMBOL)
  data/tradingview/{id}_manifest.json — resolved symbols + names

TradingView Premium: Watchlist → ⋮ → Import list → paste .txt contents
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
CONFIG_PATH = Path(os.environ.get("TV_CONFIG_PATH", ROOT / "data" / "tradingview_watchlists.json"))
OUT_DIR = Path(os.environ.get("TV_OUT_DIR", ROOT / "data" / "tradingview"))


def _load_map() -> dict[str, dict]:
    payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return dict(payload.get("companies") or payload)


def _tv_symbol(rec: dict, kite: str) -> str:
    venue = (rec.get("venue") or "").upper()
    nse = (rec.get("nse_symbol") or "").strip().upper()
    if venue == "NSE" or nse:
        sym = nse or (rec.get("kite_symbol") or kite).strip().upper()
        return f"NSE:{sym}"
    bse = (rec.get("bse_symbol") or rec.get("kite_symbol") or kite).strip().upper()
    return f"BSE:{bse}"


def _resolve(kite: str, companies: dict[str, dict]) -> tuple[str, str, str | None]:
    sym = kite.strip().upper()
    rec = companies.get(sym)
    if not rec:
        for key, row in companies.items():
            if (row.get("kite_symbol") or key).upper() == sym:
                rec = row
                sym = key
                break
    if not rec:
        return f"NSE:{sym}", sym, None
    return _tv_symbol(rec, sym), sym, rec.get("name")


def main() -> int:
    if not CONFIG_PATH.is_file():
        print(f"Missing {CONFIG_PATH}", file=sys.stderr)
        return 1
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}", file=sys.stderr)
        return 1

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies = _load_map()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_lists: dict = {}
    for list_id, spec in (config.get("watchlists") or {}).items():
        title = spec.get("title") or list_id
        note = spec.get("note") or ""
        raw_syms = spec.get("symbols") or []
        seen: set[str] = set()
        resolved: list[dict] = []
        lines: list[str] = []

        for raw in raw_syms:
            tv, kite, name = _resolve(str(raw), companies)
            if tv in seen:
                continue
            seen.add(tv)
            lines.append(tv)
            resolved.append({"tv": tv, "kite_symbol": kite, "name": name})

        txt_path = OUT_DIR / f"{list_id}.txt"
        txt_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        manifest = {
            "id": list_id,
            "title": title,
            "note": note,
            "count": len(resolved),
            "symbols": resolved,
            "txt_file": str(txt_path.relative_to(ROOT)),
        }
        (OUT_DIR / f"{list_id}_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        all_lists[list_id] = manifest
        print(f"  {list_id}: {len(resolved)} symbols → {txt_path.name}")

    index = {
        "version": config.get("version", 1),
        "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "import_help": (
            "TradingView Premium: open Watchlist panel → list menu (⋮) → "
            "Import list → paste contents of a .txt file (one NSE:SYMBOL per line)."
        ),
        "watchlists": all_lists,
    }
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote {len(all_lists)} watchlists → {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
