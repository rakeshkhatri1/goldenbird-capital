"""
Fetch NSE FII / DII cash-market activity and write data/fii_dii.json.

Usage:
  python3 build_fii_dii.py

Reads NSE reports API (session cookies required). On failure, keeps existing JSON.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import http.cookiejar
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_PATH = Path(os.environ.get("FII_DII_OUT", ROOT / "data" / "fii_dii.json"))
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_REFERER = "https://www.nseindia.com/reports/fii-dii"
USER_AGENT = os.environ.get(
    "FII_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)


def _num(v) -> float:
    if v is None or v == "" or v == "-":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return 0.0


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def fetch_rows() -> list[dict]:
    try:
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
        time.sleep(0.6)
        session.get(NSE_REFERER, timeout=25)
        time.sleep(0.4)
        resp = session.get(NSE_API, headers={"Referer": NSE_REFERER}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        opener.open(urllib.request.Request(NSE_HOME, headers=headers), timeout=25).read()
        time.sleep(0.6)
        opener.open(urllib.request.Request(NSE_REFERER, headers=headers), timeout=25).read()
        time.sleep(0.4)
        req = urllib.request.Request(
            NSE_API,
            headers={**headers, "Referer": NSE_REFERER},
        )
        with opener.open(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []


def normalize(rows: list[dict]) -> list[dict]:
    """Collapse FII + DII rows per date into one series point."""
    by_date: dict[str, dict] = {}
    for row in rows:
        category = str(row.get("category") or row.get("Category") or "").upper()
        date_raw = row.get("date") or row.get("Date") or ""
        day = _parse_date(str(date_raw))
        if not day:
            continue
        slot = by_date.setdefault(
            day,
            {
                "date": day,
                "fii_buy": 0.0,
                "fii_sell": 0.0,
                "fii_net": 0.0,
                "dii_buy": 0.0,
                "dii_sell": 0.0,
                "dii_net": 0.0,
            },
        )
        buy = _num(row.get("buyValue") or row.get("buyVal") or row.get("buy"))
        sell = _num(row.get("sellValue") or row.get("sellVal") or row.get("sell"))
        net = _num(row.get("netValue") or row.get("netVal") or row.get("net"))
        if net == 0.0 and (buy or sell):
            net = buy - sell
        if "FII" in category or "FPI" in category:
            slot["fii_buy"] = buy
            slot["fii_sell"] = sell
            slot["fii_net"] = net
        elif "DII" in category:
            slot["dii_buy"] = buy
            slot["dii_sell"] = sell
            slot["dii_net"] = net
    return sorted(by_date.values(), key=lambda x: x["date"])


def main() -> int:
    existing = {}
    if OUT_PATH.exists():
        try:
            existing = json.loads(OUT_PATH.read_text())
        except json.JSONDecodeError:
            existing = {}

    try:
        rows = fetch_rows()
        fresh = normalize(rows)
        if not fresh:
            raise RuntimeError("Empty FII/DII series from NSE")

        # NSE endpoint usually returns only the latest session — merge into history.
        by_date = {
            str(p.get("date")): p
            for p in (existing.get("series") or [])
            if isinstance(p, dict) and p.get("date")
        }
        for point in fresh:
            by_date[point["date"]] = point
        series = [by_date[k] for k in sorted(by_date.keys())][-45:]
        latest = series[-1]
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "unit": "₹ crore",
            "source": "nse",
            "source_url": NSE_REFERER,
            "note": "NSE cash-market FII/DII activity (₹ crore). History grows as you re-run this script.",
            "latest": latest,
            "series": series,
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Wrote {OUT_PATH} · {len(series)} days · latest {latest['date']}")
        print(
            f"  FII net {latest['fii_net']:+.1f} Cr · DII net {latest['dii_net']:+.1f} Cr"
        )
        return 0
    except Exception as exc:
        print(f"NSE fetch failed ({exc}). Keeping existing file if present.")
        if existing.get("series"):
            print(f"Existing series days: {len(existing['series'])}")
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
