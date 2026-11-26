"""Shared Screener.in HTML fetch and parse helpers for build_* pipelines."""
from __future__ import annotations

import html as html_module
import re
import urllib.error
import urllib.request

from screener_utils import screener_slug_candidates

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; nse_engine/2.0; +screener)"

LI_RATIO_RE = re.compile(
    r'<li class="flex flex-space-between"[^>]*>\s*<span class="name">\s*(.*?)\s*</span>'
    r".*?<span class=\"nowrap value\">(.*?)</span>\s*</li>",
    re.DOTALL | re.I,
)
HIGH_LOW_RE = re.compile(
    r"High / Low.*?<span class=\"nowrap value\">(.*?)</span>",
    re.DOTALL | re.I,
)
META_DESC_RE = re.compile(r'name="description" content="([^"]+)"', re.I)
NEXT_SECTION_RE = re.compile(r"<section\s+id=", re.I)
GROWTH_TABLE_RE = re.compile(
    r"(Compounded (?:Profit|Sales) Growth)</th>(.*?)</table>",
    re.DOTALL | re.I,
)
GROWTH_ROW_RE = re.compile(
    r"<tr>\s*<td>\s*([^<]+?)\s*</td>\s*<td>\s*([^<]+?)\s*</td>\s*</tr>",
    re.DOTALL | re.I,
)
ISSUE_LI_RE = re.compile(r"<li>([^<]{8,250})</li>", re.I)
CONCALLS_BLOCK_RE = re.compile(
    r'<div class="documents concalls[^"]*"[^>]*>(.*?)(?=<div class="documents|\Z)',
    re.DOTALL | re.I,
)
CONCALL_PERIOD_RE = re.compile(r"^([A-Za-z]{3}\s+\d{4})")


def strip_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return html_module.unescape(re.sub(r"\s+", " ", text).strip())


def parse_pct(raw: str) -> float | None:
    s = strip_html(raw).replace("%", "").replace(",", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_number(raw: str) -> float | None:
    s = strip_html(raw)
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def fetch_html(url: str, *, user_agent: str = DEFAULT_USER_AGENT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_screener_pages(slug: str, *, user_agent: str = DEFAULT_USER_AGENT) -> tuple[str, str, str]:
    """Return (html, data_basis, url). Prefers consolidated figures."""
    standalone_url = f"https://www.screener.in/company/{slug}/"
    consolidated_url = f"https://www.screener.in/company/{slug}/consolidated/"
    try:
        con_html = fetch_html(consolidated_url, user_agent=user_agent)
        if 'data-consolidated="true"' in con_html or "Consolidated Figures" in con_html:
            return con_html, "consolidated", consolidated_url
    except urllib.error.HTTPError:
        pass
    std_html = fetch_html(standalone_url, user_agent=user_agent)
    return std_html, "standalone", standalone_url


def fetch_screener_for_rec(
    rec: dict,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[str, str, str, str]:
    """Return (html, data_basis, url, slug_used). Tries alternate slugs on 404."""
    last_err: urllib.error.HTTPError | None = None
    slugs = screener_slug_candidates(rec)
    if not slugs:
        raise ValueError("no_screener_slug")
    for slug in slugs:
        try:
            page_html, basis, url = fetch_screener_pages(slug, user_agent=user_agent)
            return page_html, basis, url, slug
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue
            raise
    if last_err:
        raise last_err
    raise ValueError("no_screener_page")


def section_slice(page_html: str, section_id: str) -> str:
    m = re.search(rf'id="{re.escape(section_id)}"[^>]*>', page_html)
    if not m:
        return ""
    start = m.start()
    nxt = NEXT_SECTION_RE.search(page_html, m.end())
    end = nxt.start() if nxt else min(len(page_html), start + 150_000)
    return page_html[start:end]


def parse_results_table(section_html: str) -> tuple[list[str], dict[str, list[float | None]]]:
    rt = section_html.find("data-result-table")
    if rt < 0:
        return [], {}
    chunk = section_html[rt : rt + 120_000]
    thead = chunk.split("</thead>")[0] if "</thead>" in chunk else chunk[:5000]
    dates = re.findall(r'data-date-key="([^"]+)"', thead)
    if not dates:
        dates = re.findall(r'data-date-key="([^"]+)"', chunk[:8000])
    rows: dict[str, list[float | None]] = {}
    tbody = chunk.split("<tbody>")[1].split("</tbody>")[0] if "<tbody>" in chunk else chunk
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if len(cells) < 2:
            continue
        label = strip_html(cells[0]).replace("+", "").strip()
        vals: list[float | None] = []
        for c in cells[1:]:
            t = re.sub(r"<[^>]+>", "", c).strip().replace(",", "")
            if t in ("", "-", "—"):
                vals.append(None)
            else:
                try:
                    vals.append(float(t))
                except ValueError:
                    vals.append(None)
        if label and dates:
            rows[label] = vals[: len(dates)]
    return dates, rows


def parse_high_low_from_value(val_raw: str) -> tuple[float | None, float | None]:
    nums = re.findall(r'<span class="number">([\d,.]+)</span>', val_raw)
    if len(nums) >= 2:
        return parse_number(nums[0]), parse_number(nums[1])
    chunk = strip_html(val_raw)
    parts = re.split(r"\s*/\s*", chunk)
    if len(parts) == 2:
        return parse_number(parts[0]), parse_number(parts[1])
    return None, None


def parse_market_cap(page_html: str) -> float | None:
    for name_raw, val_raw in LI_RATIO_RE.findall(page_html):
        if strip_html(name_raw).lower() == "market cap":
            return parse_number(val_raw)
    return None


def parse_top_ratios(page_html: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name_raw, val_raw in LI_RATIO_RE.findall(page_html):
        name = strip_html(name_raw).lower()
        if name == "market cap":
            out["market_cap_cr"] = parse_number(val_raw)
        elif name == "current price":
            out["screener_price"] = parse_number(val_raw)
        elif name == "stock p/e":
            out["pe_ttm"] = parse_number(val_raw)
        elif name == "roce":
            out["roce_pct"] = parse_pct(val_raw)
        elif name == "roe":
            out["roe_pct"] = parse_pct(val_raw)
        elif name == "book value":
            out["book_value"] = parse_number(val_raw)
        elif name == "dividend yield":
            out["dividend_yield_pct"] = parse_pct(val_raw)
        elif name == "face value":
            out["face_value"] = parse_number(val_raw)
        elif name == "high / low":
            high, low = parse_high_low_from_value(val_raw)
            out["high_52w"] = high
            out["low_52w"] = low
    if out.get("high_52w") is None:
        m = HIGH_LOW_RE.search(page_html)
        if m:
            high, low = parse_high_low_from_value(m.group(1))
            out["high_52w"] = high
            out["low_52w"] = low
    return out


def parse_compounded_growth(page_html: str) -> dict[str, float | None]:
    """Return profit_growth_* and sales_growth_* keys (ttm, 3y, 5y, 10y)."""
    out: dict[str, float | None] = {}
    key_map = {
        "ttm": "ttm",
        "3 years": "3y",
        "5 years": "5y",
        "10 years": "10y",
    }
    for m in GROWTH_TABLE_RE.finditer(page_html):
        kind = "profit" if "profit" in m.group(1).lower() else "sales"
        block = m.group(2)
        for row_m in GROWTH_ROW_RE.finditer(block):
            label = strip_html(row_m.group(1)).lower().rstrip(":")
            val = parse_pct(row_m.group(2))
            suffix = key_map.get(label)
            if suffix and val is not None:
                out[f"{kind}_growth_{suffix}_pct"] = val
    return out


def parse_shareholding_quarterly(page_html: str) -> dict[str, float | None]:
    """Latest % and 4-quarter change for promoter / FII / DII / public."""
    chunk = section_slice(page_html, "shareholding")
    if not chunk:
        return {}
    cat_map = {
        "promoters": "promoter",
        "foreignInstitutions": "fii",
        "domesticInstitutions": "dii",
        "public": "public",
        "government": "govt",
    }
    out: dict[str, float | None] = {}
    for js_key, prefix in cat_map.items():
        idx = chunk.find(f"showShareholders('{js_key}'")
        if idx < 0:
            continue
        pcts = [float(x) for x in re.findall(r"([\d.]+)%", chunk[idx : idx + 6000])]
        if not pcts:
            continue
        out[f"{prefix}_pct"] = pcts[-1]
        if len(pcts) >= 2:
            out[f"{prefix}_change_4q_pp"] = round(pcts[-1] - pcts[0], 2)
    return out


def parse_screener_notes(page_html: str) -> list[str]:
    """All bullet notes Screener shows under the company header (pros + cons)."""
    notes: list[str] = []
    seen: set[str] = set()
    for li in ISSUE_LI_RE.findall(page_html[:140_000]):
        text = strip_html(li)
        key = text.lower()
        if len(text) >= 12 and key not in seen:
            seen.add(key)
            notes.append(text)
    return notes[:14]


def latest_row_value(rows: dict[str, list[float | None]], *labels: str) -> float | None:
    """Last non-null value from the first matching P&L/BS/CF row label."""
    targets = [lb.lower() for lb in labels]
    for label, vals in rows.items():
        low = label.lower()
        if not any(t in low for t in targets):
            continue
        clean = [float(v) for v in vals if v is not None]
        if clean:
            return clean[-1]
    return None


def row_cagr_pct(rows: dict[str, list[float | None]], label_substr: str, years: int = 3) -> float | None:
    for label, vals in rows.items():
        if label_substr.lower() not in label.lower():
            continue
        clean = [float(v) for v in vals if v is not None]
        if len(clean) < years + 1:
            continue
        old, new = clean[-(years + 1)], clean[-1]
        if old <= 0 or new <= 0:
            return None
        return round(((new / old) ** (1.0 / years) - 1.0) * 100.0, 1)
    return None


def parse_meta_description(page_html: str) -> dict[str, float | None]:
    m = META_DESC_RE.search(page_html)
    if not m:
        return {}
    desc = m.group(1)
    out: dict[str, float | None] = {}
    for pattern, key in (
        (r"Promoter Holding:\s*([\d.]+)%", "promoter_pct"),
        (r"Revenue:\s*([\d.]+)\s*Cr", "revenue_ttm_cr"),
        (r"Profit:\s*([\d.]+)\s*Cr", "profit_ttm_cr"),
    ):
        hit = re.search(pattern, desc, re.I)
        if hit:
            out[key] = float(hit.group(1))
    return out


def parse_concalls(page_html: str) -> dict:
    """
    Screener documents → Concalls block.
    Returns whether the company has any concall entries listed on Screener.
    """
    out: dict = {
        "has_concall": False,
        "concall_count": 0,
        "latest_concall_period": None,
        "latest_concall_has_transcript": False,
        "concall_section_present": False,
    }
    doc = section_slice(page_html, "documents")
    if not doc:
        return out
    m = CONCALLS_BLOCK_RE.search(doc)
    if not m:
        return out
    out["concall_section_present"] = True
    items = re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.DOTALL)
    out["concall_count"] = len(items)
    out["has_concall"] = len(items) > 0
    if items:
        first_html = items[0]
        first_text = strip_html(first_html)
        pm = CONCALL_PERIOD_RE.match(first_text)
        if pm:
            out["latest_concall_period"] = pm.group(1)
        out["latest_concall_has_transcript"] = "transcript" in first_html.lower()
    return out
