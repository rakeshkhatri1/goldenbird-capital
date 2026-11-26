"""
Pull Motilal Oswal research into data/research_articles.json.

Sources
  1. Motilal official spreadsheet feed (sector / company / economy / thematic)
     https://www.motilaloswal.com/spreadsheets/research-report.json
  2. Moneycontrol Motilal tag pages (readable stock-call articles)

Usage:
  python3 build_research_articles.py

Re-run anytime (or cron) — new reports merge into history by id.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT_PATH = Path(os.environ.get("RESEARCH_ARTICLES_OUT", ROOT / "data" / "research_articles.json"))
MOTILAL_SHEET = "https://www.motilaloswal.com/spreadsheets/research-report.json"
MOTILAL_HUB = "https://www.motilaloswal.com/research-reports"
MC_TAG_PAGES = [
    "https://www.moneycontrol.com/news/tags/motilal-oswal.html",
    "https://www.moneycontrol.com/news/tags/motilal-oswal.html/page-2/",
    "https://www.moneycontrol.com/news/tags/motilal-oswal.html/page-3/",
]
USER_AGENT = os.environ.get(
    "RESEARCH_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
MAX_HISTORY = int(os.environ.get("RESEARCH_MAX_HISTORY", "250"))


def _get(url: str, timeout: int = 45) -> str:
    try:
        import requests

        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html,*/*",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html,*/*",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def _iso_day(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:10]


def _bucket(report_type: str, title: str = "") -> str:
    rt = (report_type or "").strip().lower()
    tl = (title or "").lower()
    if rt in {"sector", "industry"} or "sector" in tl:
        return "sector"
    if rt in {"economy", "macro"} or any(k in tl for k in ("economy", "ecoscope", "strategy")):
        return "sector" if "strategy" in tl or "sector" in tl else "economy"
    if rt in {"thematic"}:
        return "thematic"
    if rt in {"expert-opinion", "company", "equity"}:
        return "stock"
    if re.search(r"\b(buy|sell|neutral|accumulate|reduce)\b", tl):
        return "stock"
    return "stock"


def _slug(firm: str, source: str, key: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", f"{firm}-{source}-{key}").strip("-").lower()
    return base[:120] or f"{firm}-{source}"


def fetch_motilal_sheet() -> list[dict[str, Any]]:
    raw = _get(MOTILAL_SHEET)
    payload = json.loads(raw)
    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = unescape(str(row.get("reportTitle") or "").strip())
        if not title:
            continue
        report_id = str(row.get("reportId") or "").strip()
        pdf = str(row.get("pdfFileName") or "").strip()
        report_type = str(row.get("reportType") or "").strip()
        report_name = str(row.get("reportName") or "").strip()
        day = _iso_day(str(row.get("reportDate") or ""))
        summary = unescape(
            " ".join(
                p
                for p in [
                    str(row.get("description") or "").strip(),
                    str(row.get("bulletpoint1") or "").strip(),
                ]
                if p
            )
        )
        # Hub/report page is always openable; CloudFront PDFs are often gated.
        report_page = (
            f"https://www.motilaloswal.com/report/{report_id}" if report_id else MOTILAL_HUB
        )
        out.append(
            {
                "id": _slug("motilal", "sheet", report_id or title),
                "firm": "Motilal Oswal",
                "firm_id": "motilal",
                "bucket": _bucket(report_type, title),
                "kind": report_name or report_type or "Research",
                "title": title,
                "summary": summary[:420],
                "date": day,
                "url": report_page,
                "hub_url": MOTILAL_HUB,
                "pdf_url": pdf if pdf.startswith("http") else "",
                "source": "motilal_sheet",
                "source_id": report_id,
            }
        )
    return out


def fetch_moneycontrol_motilal() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_url in MC_TAG_PAGES:
        try:
            html = _get(page_url)
        except Exception:
            continue
        # Schema.org ItemList is the most stable extract.
        for m in re.finditer(
            r'"@type"\s*:\s*"ListItem"[^{}]*?"url"\s*:\s*"([^"]+)"[^{}]*?"name"\s*:\s*"([^"]+)"',
            html,
            flags=re.I | re.S,
        ):
            href, title = m.group(1), unescape(m.group(2)).strip()
            if href in seen:
                continue
            if "/tags/" in href:
                continue
            seen.add(href)
            tl = title.lower()
            # Skip pure corporate / fund PR unless it is a rating note.
            is_call = bool(
                re.search(r"\b(buy|sell|neutral|accumulate|reduce|hold)\b", tl)
                or "/recommendations/" in href
                or "target of" in tl
            )
            if not is_call and "motilal" not in tl:
                continue
            bucket = "stock" if is_call else _bucket("", title)
            out.append(
                {
                    "id": _slug("motilal", "mc", href.rsplit("-", 1)[-1].replace(".html", "")),
                    "firm": "Motilal Oswal",
                    "firm_id": "motilal",
                    "bucket": bucket,
                    "kind": "Stock call" if is_call else "Article",
                    "title": title,
                    "summary": "Coverage note via Moneycontrol (Motilal Oswal).",
                    "date": "",
                    "url": href,
                    "hub_url": MOTILAL_HUB,
                    "pdf_url": "",
                    "source": "moneycontrol",
                    "source_id": href,
                }
            )
        # Fallback: title attributes on article anchors.
        if not out:
            for href, title in re.findall(
                r'href="(https://www\.moneycontrol\.com/news/[^"]+)"[^>]*title="([^"]+)"',
                html,
            ):
                title = unescape(title).strip()
                if href in seen or "/tags/" in href:
                    continue
                if "motilal" not in title.lower() and "motilal" not in href.lower():
                    continue
                seen.add(href)
                out.append(
                    {
                        "id": _slug("motilal", "mc", href.rsplit("-", 1)[-1].replace(".html", "")),
                        "firm": "Motilal Oswal",
                        "firm_id": "motilal",
                        "bucket": _bucket("", title),
                        "kind": "Article",
                        "title": title,
                        "summary": "Coverage note via Moneycontrol (Motilal Oswal).",
                        "date": "",
                        "url": href,
                        "hub_url": MOTILAL_HUB,
                        "pdf_url": "",
                        "source": "moneycontrol",
                        "source_id": href,
                    }
                )
    return out


def merge_articles(
    existing: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in existing + fresh:
        if not isinstance(row, dict):
            continue
        key = str(row.get("id") or "").strip()
        if not key:
            continue
        prev = by_id.get(key)
        if not prev:
            by_id[key] = row
            continue
        # Prefer richer / newer fields on refresh.
        merged = {**prev, **{k: v for k, v in row.items() if v not in (None, "")}}
        by_id[key] = merged

    def sort_key(a: dict[str, Any]) -> tuple:
        return (str(a.get("date") or ""), str(a.get("title") or ""))

    ordered = sorted(by_id.values(), key=sort_key, reverse=True)
    return ordered[:MAX_HISTORY]


def main() -> int:
    existing_payload: dict[str, Any] = {}
    if OUT_PATH.exists():
        try:
            existing_payload = json.loads(OUT_PATH.read_text())
        except json.JSONDecodeError:
            existing_payload = {}
    existing = existing_payload.get("articles") if isinstance(existing_payload, dict) else []
    if not isinstance(existing, list):
        existing = []

    fresh: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        sheet = fetch_motilal_sheet()
        fresh.extend(sheet)
        print(f"Motilal sheet: {len(sheet)} reports")
    except Exception as exc:
        errors.append(f"motilal_sheet: {exc}")
        print(f"Motilal sheet failed: {exc}")

    try:
        mc = fetch_moneycontrol_motilal()
        fresh.extend(mc)
        print(f"Moneycontrol Motilal: {len(mc)} articles")
    except Exception as exc:
        errors.append(f"moneycontrol: {exc}")
        print(f"Moneycontrol fetch failed: {exc}")

    if not fresh and not existing:
        print("No articles fetched and no existing file.")
        return 1

    articles = merge_articles(existing, fresh)
    stock_n = sum(1 for a in articles if a.get("bucket") == "stock")
    sector_n = sum(1 for a in articles if a.get("bucket") == "sector")
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Auto-built Motilal research feed. Re-run build_research_articles.py "
            "(or cron) whenever you want the latest."
        ),
        "sources": [
            {"id": "motilal_sheet", "url": MOTILAL_SHEET},
            {"id": "moneycontrol", "url": MC_TAG_PAGES[0]},
        ],
        "counts": {
            "total": len(articles),
            "stock": stock_n,
            "sector": sector_n,
            "other": len(articles) - stock_n - sector_n,
        },
        "errors": errors,
        "articles": articles,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {OUT_PATH} · {len(articles)} articles "
        f"(stock {stock_n} · sector {sector_n})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
