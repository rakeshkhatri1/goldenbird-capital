"""
Build Screener → Zerodha (Kite) company map from data/screener_symbols.txt.

Output: data/screener_company_map.json

Each company row has a canonical symbol (no -BE/-SM/-ST series suffix), the Kite
tradingsymbol to fetch (may include -RR for REITs), venue, and all Screener ids
(alpha slugs + numeric BSE scrip codes) that refer to the same instrument.

Usage:
  .venv/bin/python3 build_screener_map.py
  SCREENER_SYMBOLS_PATH=data/my_list.txt .venv/bin/python3 build_screener_map.py

Requires KITE_API_KEY + KITE_ACCESS_TOKEN in .env (run kite_session.py if expired).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv(Path(__file__).resolve().parent / ".env")

SCREENER_SYMBOLS_PATH = os.environ.get(
    "SCREENER_SYMBOLS_PATH",
    str(Path(__file__).resolve().parent / "data" / "screener_symbols.txt"),
)
OUTPUT_PATH = os.environ.get(
    "SCREENER_MAP_PATH",
    str(Path(__file__).resolve().parent / "data" / "screener_company_map.json"),
)

# Series suffixes on NSE/BSE EQ (not debt). Used to resolve Screener slug → Kite symbol.
SERIES_SUFFIXES = (
    "-BE",
    "-SM",
    "-ST",
    "-BZ",
    "-IV",
    "-RR",
    "-SZ",
    "-HCIL",
    "-DVR",
)

# Debt / NCD patterns — excluded from stock map if they appear in input.
DEBT_HYPHEN_RE = re.compile(
    r"-(SG|GS|TB|GB|NC|N\d+|NE|DB|DP|MF|GB|TR|PB|CB|CP|ZF)\b",
    re.I,
)


def _kite_client() -> KiteConnect:
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
    if not api_key or not token:
        print("Missing KITE_API_KEY or KITE_ACCESS_TOKEN in .env", file=sys.stderr)
        sys.exit(1)
    k = KiteConnect(api_key=api_key)
    k.set_access_token(token)
    return k


def build_eq_index(kite: KiteConnect, exchange: str) -> dict[str, dict]:
    seg = exchange
    out: dict[str, dict] = {}
    for inst in kite.instruments(exchange):
        if inst.get("instrument_type") != "EQ" or inst.get("segment") != seg:
            continue
        sym = (inst.get("tradingsymbol") or "").strip().upper()
        if not sym:
            continue
        out[sym] = {
            "instrument_token": int(inst["instrument_token"]),
            "exchange_token": int(inst.get("exchange_token") or 0),
            "name": (inst.get("name") or "").strip(),
            "exchange": exchange,
        }
    return out


def build_bse_scrip_index(bse_idx: dict[str, dict]) -> dict[str, str]:
    """BSE scrip code (exchange_token) → tradingsymbol."""
    out: dict[str, str] = {}
    for sym, meta in bse_idx.items():
        code = meta.get("exchange_token")
        if code:
            out[str(code)] = sym
    return out


def is_debt_symbol(sym: str) -> bool:
    if "-" not in sym:
        return False
    return bool(DEBT_HYPHEN_RE.search(sym))


def canonical_symbol(kite_symbol: str) -> str:
    """Strip known EQ series suffix for a stable company key."""
    u = kite_symbol.upper()
    for suf in SERIES_SUFFIXES:
        if u.endswith(suf):
            return u[: -len(suf)]
    return u


def resolve_on_exchange(sym: str, idx: dict[str, dict]) -> tuple[str, dict] | None:
    u = sym.upper()
    if u in idx:
        return u, idx[u]
    for suf in SERIES_SUFFIXES:
        candidate = u + suf
        if candidate in idx:
            return candidate, idx[candidate]
    return None


def resolve_screener_id(
    screener_id: str,
    nse_idx: dict[str, dict],
    bse_idx: dict[str, dict],
    bse_scrip: dict[str, str],
) -> dict | None:
    sid = screener_id.strip().upper()
    if not sid or is_debt_symbol(sid):
        return None

    # Numeric line = BSE scrip code on Screener
    if sid.isdigit():
        bse_sym = bse_scrip.get(sid)
        if not bse_sym:
            return None
        meta = bse_idx[bse_sym]
        return {
            "screener_id": sid,
            "kite_symbol": bse_sym,
            "canonical_symbol": canonical_symbol(bse_sym),
            "venue": "BSE",
            "instrument_token": meta["instrument_token"],
            "name": meta["name"],
            "bse_scrip_code": sid,
            "nse_symbol": bse_sym if bse_sym in nse_idx else None,
            "bse_symbol": bse_sym,
        }

    # Alpha slug — prefer NSE, then BSE
    hit = resolve_on_exchange(sid, nse_idx)
    venue = "NSE"
    if hit:
        kite_sym, meta = hit
    else:
        hit = resolve_on_exchange(sid, bse_idx)
        venue = "BSE"
        if not hit:
            return None
        kite_sym, meta = hit

    bse_code = None
    if kite_sym in bse_idx:
        et = bse_idx[kite_sym].get("exchange_token")
        if et:
            bse_code = str(et)

    return {
        "screener_id": sid,
        "kite_symbol": kite_sym,
        "canonical_symbol": canonical_symbol(kite_sym),
        "venue": venue,
        "instrument_token": meta["instrument_token"],
        "name": meta["name"],
        "bse_scrip_code": bse_code,
        "nse_symbol": kite_sym if kite_sym in nse_idx else None,
        "bse_symbol": kite_sym if kite_sym in bse_idx else None,
    }


def load_screener_lines(path: Path) -> list[str]:
    lines = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        lines.append(s)
    return lines


def merge_companies(resolved: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Group by kite_symbol (one fetch target per company)."""
    by_kite: dict[str, dict] = {}
    for row in resolved:
        key = row["kite_symbol"]
        if key not in by_kite:
            by_kite[key] = {
                "canonical_symbol": row["canonical_symbol"],
                "kite_symbol": row["kite_symbol"],
                "venue": row["venue"],
                "instrument_token": row["instrument_token"],
                "name": row["name"],
                "nse_symbol": row.get("nse_symbol"),
                "bse_symbol": row.get("bse_symbol"),
                "bse_scrip_codes": [],
                "screener_ids": [],
            }
        rec = by_kite[key]
        rec["screener_ids"].append(row["screener_id"])
        if row.get("bse_scrip_code") and row["bse_scrip_code"] not in rec["bse_scrip_codes"]:
            rec["bse_scrip_codes"].append(row["bse_scrip_code"])
        # Prefer NSE venue when dual-listed
        if row["venue"] == "NSE":
            rec["venue"] = "NSE"
            rec["instrument_token"] = row["instrument_token"]
        if row.get("nse_symbol"):
            rec["nse_symbol"] = row["nse_symbol"]
        if row.get("bse_symbol"):
            rec["bse_symbol"] = row["bse_symbol"]
        if row.get("name"):
            rec["name"] = row["name"]

    for rec in by_kite.values():
        rec["screener_ids"] = sorted(set(rec["screener_ids"]))
        rec["bse_scrip_codes"] = sorted(set(rec["bse_scrip_codes"]))

    # Canonical index for lookup by screener slug / numeric code
    companies = {}
    for kite_sym, rec in sorted(by_kite.items(), key=lambda x: x[1]["canonical_symbol"]):
        companies[rec["canonical_symbol"]] = rec

    return companies, []


def main() -> None:
    src = Path(SCREENER_SYMBOLS_PATH)
    if not src.is_file():
        print(f"Missing input: {src}", file=sys.stderr)
        sys.exit(1)

    lines = load_screener_lines(src)
    alpha_n = sum(1 for s in lines if not s.isdigit())
    numeric_n = len(lines) - alpha_n

    print(f"Loading Kite instruments (NSE + BSE)...")
    kite = _kite_client()
    nse_idx = build_eq_index(kite, "NSE")
    bse_idx = build_eq_index(kite, "BSE")
    bse_scrip = build_bse_scrip_index(bse_idx)
    print(f"  NSE EQ: {len(nse_idx):,} | BSE EQ: {len(bse_idx):,} | BSE scrip codes: {len(bse_scrip):,}")

    resolved: list[dict] = []
    unmapped: list[dict] = []

    for sid in lines:
        row = resolve_screener_id(sid, nse_idx, bse_idx, bse_scrip)
        if row:
            resolved.append(row)
        else:
            unmapped.append({"screener_id": sid, "reason": "not_on_kite_or_debt"})

    companies, _ = merge_companies(resolved)

    # Cache coverage
    cache_dir = Path(__file__).resolve().parent / "data" / "historical_cache"
    cached = 0
    for rec in companies.values():
        ks = rec["kite_symbol"]
        rec["in_cache"] = (cache_dir / f"{ks}.json").is_file()
        if rec["in_cache"]:
            cached += 1

    out = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": str(src),
        "stats": {
            "input_lines": len(lines),
            "input_alpha": alpha_n,
            "input_numeric_bse_codes": numeric_n,
            "resolved_lines": len(resolved),
            "unmapped_lines": len(unmapped),
            "unique_companies": len(companies),
            "companies_in_cache": cached,
        },
        "companies": companies,
        "unmapped": unmapped,
    }

    out_path = Path(OUTPUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    unmapped_path = out_path.parent / "screener_map_unmapped.txt"
    unmapped_path.write_text(
        "\n".join(u["screener_id"] for u in unmapped) + ("\n" if unmapped else ""),
        encoding="utf-8",
    )

    s = out["stats"]
    print()
    print("=== Screener company map ===")
    print(f"Input lines:        {s['input_lines']:,} ({s['input_alpha']:,} alpha + {s['input_numeric_bse_codes']:,} numeric)")
    print(f"Resolved:           {s['resolved_lines']:,}")
    print(f"Unique companies:   {s['unique_companies']:,}")
    print(f"Already in cache:   {s['companies_in_cache']:,}")
    print(f"Unmapped:           {s['unmapped_lines']:,}  → {unmapped_path}")
    print(f"Wrote:              {out_path}")


if __name__ == "__main__":
    main()
