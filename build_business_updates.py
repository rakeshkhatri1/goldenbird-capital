"""
Fetch NSE corporate announcements filtered to business updates; optional Gemini summaries.

Reads:  data/screener_company_map.json (symbol names)
Writes: data/business_updates.json

Usage:
  python3 build_business_updates.py
  BU_DAYS=30 BU_GEMINI=1 python3 build_business_updates.py   # summarize new items
  BU_MAX=10 BU_GEMINI=0 python3 build_business_updates.py   # smoke test, no AI

Resume merges by seq_id; re-run to pick up new exchange filings.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from business_update_utils import summarize_business_update
from universe_filter import load_eligible_symbols, load_known_ineligible_symbols

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
OUT_PATH = Path(os.environ.get("BU_OUT_PATH", ROOT / "data" / "business_updates.json"))
DAYS = int(os.environ.get("BU_DAYS", "45"))
MAX_ITEMS = os.environ.get("BU_MAX", "").strip()
USE_GEMINI = os.environ.get("BU_GEMINI", "0").strip().lower() in ("1", "true", "yes", "on")
GEMINI_SLEEP = float(os.environ.get("BU_GEMINI_SLEEP", "1.5"))
RESUME = os.environ.get("BU_RESUME", "1").strip().lower() in ("1", "true", "yes", "on")
FORCE_RESUMMARIZE = os.environ.get("BU_FORCE", os.environ.get("BU_RESUMMARIZE", "0")).strip().lower() in (
    "1", "true", "yes", "on",
)
SYMBOL_FILTER = {
    s.strip().upper()
    for s in os.environ.get("BU_SYMBOLS", "").split(",")
    if s.strip()
}
USER_AGENT = os.environ.get(
    "BU_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

NSE_HOME = "https://www.nseindia.com/"
NSE_ANN_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_date}&to_date={to_date}"
)
NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"


def _nse_date(d: datetime) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_sort_date(raw: str) -> str:
    """Normalize to ISO-ish UTC string for sorting."""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return raw


def is_business_update(item: dict) -> bool:
    desc = (item.get("desc") or "").lower()
    text = (item.get("attchmntText") or "").lower()
    if "business update" in text:
        return True
    if "provisional business update" in text:
        return True
    if "interim business update" in text:
        return True
    if "company's business update" in text:
        return True
    if "monthly business" in desc:
        return True
    if desc in ("business updates", "monthly business updates", "interim business updates"):
        return True
    return False


def _nse_opener() -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(NSE_HOME, headers=headers)
    opener.open(req, timeout=20).read()
    return opener


def _fetch_via_requests(url: str) -> list[dict]:
    import requests

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    session.get(NSE_HOME, timeout=25)
    time.sleep(0.8)
    session.get(NSE_REFERER, timeout=25)
    time.sleep(0.5)
    resp = session.get(
        url,
        headers={"Referer": NSE_REFERER},
        timeout=90,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected NSE payload type: {type(payload)}")
    return payload


def _fetch_via_curl(url: str) -> list[dict]:
    cookie_jar = ROOT / "data" / ".nse_cookies.txt"
    cookie_jar.parent.mkdir(parents=True, exist_ok=True)
    common = [
        "curl",
        "-sS",
        "-c",
        str(cookie_jar),
        "-b",
        str(cookie_jar),
        "-H",
        f"User-Agent: {USER_AGENT}",
    ]
    subprocess.run([*common, NSE_HOME, "-o", os.devnull], check=True, timeout=30)
    time.sleep(1)
    proc = subprocess.run(
        [
            *common,
            "-H",
            "Accept: application/json",
            "-H",
            f"Referer: {NSE_REFERER}",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(proc.stdout)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected NSE payload type: {type(payload)}")
    return payload


def _fetch_via_urllib(url: str) -> list[dict]:
    opener = _nse_opener()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": NSE_REFERER,
    }
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected NSE payload type: {type(payload)}")
    return payload


def fetch_nse_announcements(from_dt: datetime, to_dt: datetime) -> list[dict]:
    url = NSE_ANN_URL.format(from_date=_nse_date(from_dt), to_date=_nse_date(to_dt))
    errors: list[str] = []
    for name, fn in (
        ("requests", _fetch_via_requests),
        ("curl", _fetch_via_curl),
        ("urllib", _fetch_via_urllib),
    ):
        try:
            return fn(url)
        except Exception as e:
            errors.append(f"{name}: {e}")
            time.sleep(1.5)
    raise RuntimeError("NSE fetch failed — " + " | ".join(errors[-3:]))


def load_company_names() -> dict[str, str]:
    if not MAP_PATH.exists():
        return {}
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    companies = data.get("companies") or data
    names: dict[str, str] = {}
    if isinstance(companies, dict):
        for sym, rec in companies.items():
            if isinstance(rec, dict):
                name = rec.get("name") or rec.get("company_name") or ""
                if name:
                    names[str(sym).upper()] = str(name)
    return names


def load_existing() -> dict[str, dict]:
    if not RESUME or not OUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        updates = data.get("updates") or []
        return {str(u.get("seq_id")): u for u in updates if u.get("seq_id")}
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_item(item: dict, name_map: dict[str, str]) -> dict:
    sym = str(item.get("symbol") or "").upper()
    subject = (item.get("attchmntText") or "").strip()
    return {
        "seq_id": str(item.get("seq_id") or ""),
        "symbol": sym,
        "name": name_map.get(sym) or (item.get("sm_name") or sym),
        "desc": (item.get("desc") or "").strip(),
        "announced_at": _parse_sort_date(item.get("sort_date") or item.get("an_dt") or ""),
        "sort_date": (item.get("sort_date") or "").strip(),
        "subject": subject,
        "attachment_url": (item.get("attchmntFile") or "").strip() or None,
        "isin": (item.get("sm_isin") or "").strip() or None,
    }


def main() -> int:
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=DAYS)
    print(f"Fetching NSE announcements {_nse_date(from_dt)} → {_nse_date(to_dt)}…", flush=True)

    try:
        raw = fetch_nse_announcements(from_dt, to_dt)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
        print(f"NSE fetch failed: {e}", file=sys.stderr)
        return 1

    filtered = [x for x in raw if is_business_update(x)]
    print(f"Total announcements: {len(raw):,} | business updates: {len(filtered):,}", flush=True)

    ineligible = load_known_ineligible_symbols()
    if ineligible:
        before = len(filtered)
        filtered = [
            x for x in filtered
            if str(x.get("symbol") or "").upper() not in ineligible
        ]
        print(
            f"Universe filter (drop known <₹100 Cr): {before:,} → {len(filtered):,} filings "
            f"({len(ineligible):,} ineligible in mcap index)",
            flush=True,
        )

    name_map = load_company_names()
    existing = load_existing()
    merged: dict[str, dict] = dict(existing)

    new_count = 0
    for item in filtered:
        seq = str(item.get("seq_id") or "")
        if not seq:
            continue
        if seq in merged:
            rec = merged[seq]
            norm = normalize_item(item, name_map)
            for k in ("subject", "attachment_url", "desc", "sort_date", "announced_at", "name"):
                if norm.get(k):
                    rec[k] = norm[k]
            merged[seq] = rec
            continue
        rec = normalize_item(item, name_map)
        rec["summary"] = None
        rec["highlights"] = []
        rec["summarized_at"] = None
        merged[seq] = rec
        new_count += 1

    eligible = load_eligible_symbols(strict=False)
    updates = sorted(
        merged.values(),
        key=lambda u: u.get("announced_at") or u.get("sort_date") or "",
        reverse=True,
    )

    gemini_limit = int(MAX_ITEMS) if MAX_ITEMS.isdigit() else None
    gemini_ok = 0
    if USE_GEMINI:
        try:
            from gemini_client import GeminiError
        except ImportError:
            print("Gemini client unavailable", file=sys.stderr)
            return 1

        pending = [u for u in updates if not u.get("summary")]
        if FORCE_RESUMMARIZE:
            pending = list(updates)
        if SYMBOL_FILTER:
            pending = [u for u in pending if u.get("symbol") in SYMBOL_FILTER]
        if gemini_limit:
            pending = pending[:gemini_limit]
        print(f"Gemini summarize: {len(pending)} pending…", flush=True)
        for i, rec in enumerate(pending, 1):
            try:
                result = summarize_business_update(rec)
                rec["summary"] = result.get("summary")
                rec["highlights"] = result.get("highlights") or []
                rec["source_kind"] = result.get("source_kind")
                rec["source_chars"] = result.get("source_chars")
                rec["summarized_at"] = datetime.now(timezone.utc).isoformat()
                gemini_ok += 1
                kind = result.get("source_kind") or "?"
                chars = result.get("source_chars") or 0
                print(
                    f"  [{i}/{len(pending)}] {rec.get('symbol')} OK ({kind}, {chars} chars)",
                    flush=True,
                )
            except Exception as e:
                print(f"  [{i}/{len(pending)}] {rec.get('symbol')} skip: {e}", flush=True)
            if i < len(pending):
                time.sleep(GEMINI_SLEEP)

    if eligible:
        updates = [u for u in updates if str(u.get("symbol") or "").upper() in eligible]

    with_summary = sum(1 for u in updates if u.get("summary"))
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": _nse_date(from_dt),
        "date_to": _nse_date(to_dt),
        "stats": {
            "total": len(updates),
            "new_this_run": new_count,
            "with_summary": with_summary,
            "gemini_this_run": gemini_ok,
        },
        "updates": updates,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n=== Business updates ===\n"
        f"Stored: {len(updates)} (new {new_count}) | summarized: {with_summary}\n"
        f"Wrote: {OUT_PATH}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
