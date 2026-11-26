"""
Shared helpers for NSE business-update PDF fetch, text extraction, and Gemini summaries.
"""
from __future__ import annotations

import io
import re
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gemini_client import GeminiClient

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
MAX_SOURCE_CHARS = int(__import__("os").environ.get("BU_MAX_SOURCE_CHARS", "14000"))


def _download_bytes(url: str) -> bytes:
    errors: list[str] = []
    try:
        import requests

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        session.get("https://www.nseindia.com/", timeout=20)
        resp = session.get(
            url,
            headers={"Referer": NSE_REFERER, "Accept": "*/*"},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        errors.append(f"requests: {e}")

    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "-L",
                "-H",
                f"User-Agent: {USER_AGENT}",
                "-H",
                f"Referer: {NSE_REFERER}",
                url,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return proc.stdout
    except Exception as e:
        errors.append(f"curl: {e}")

    raise RuntimeError("attachment download failed — " + " | ".join(errors))


def extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf not installed — pip install pypdf") from e

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        chunk = page.extract_text() or ""
        if chunk.strip():
            parts.append(chunk)
    text = "\n".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_attachment_text(url: str | None) -> tuple[str, str]:
    """Return (source_kind, text) where source_kind is pdf|none."""
    if not url:
        return "none", ""
    url = url.strip()
    lower = url.lower()
    if not (lower.endswith(".pdf") or "/corporate/" in lower):
        return "none", ""

    data = _download_bytes(url)
    if not data.startswith(b"%PDF"):
        return "none", ""
    text = extract_pdf_text(data)
    if len(text) < 80:
        return "pdf_empty", text
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS] + "\n\n[…document truncated…]"
    return "pdf", text


def build_source_document(record: dict) -> tuple[str, str]:
    """Build full source text from PDF (preferred) + exchange intimation."""
    subject = (record.get("subject") or "").strip()
    pdf_kind, pdf_text = fetch_attachment_text(record.get("attachment_url"))
    blocks: list[str] = []
    if subject:
        blocks.append(f"Exchange intimation:\n{subject}")
    if pdf_text:
        blocks.append(f"Business update document ({pdf_kind}):\n{pdf_text}")
    source = "\n\n".join(blocks).strip()
    return pdf_kind, source


def build_business_update_prompt(record: dict, source_text: str) -> str:
    return "\n".join(
        [
            f"Company: {record.get('name') or record.get('symbol')}",
            f"Symbol: {record.get('symbol')}",
            f"Filing type: {record.get('desc')}",
            f"Date: {record.get('sort_date')}",
            "",
            "SOURCE DOCUMENT:",
            source_text or "(no attachment text — use exchange intimation only)",
            "",
            "Task: Write a detailed equity-research style summary of this Indian NSE business update.",
            "Extract every quantitative metric: revenue/sales growth (YoY and QoQ), SSSG, volumes,",
            "AUM/disbursements, store count, segment splits, margins, market share, guidance.",
            "",
            "Output exactly two sections:",
            "",
            "SUMMARY:",
            "5-8 sentences. Lead with the reporting period. Include specific % and ₹/ crore figures.",
            "Cover operational drivers, headwinds, expansion, and forward outlook if stated.",
            "",
            "HIGHLIGHTS:",
            "- 6-10 bullet lines starting with '- '. Each bullet must contain at least one number",
            "  or concrete operational fact from the document (growth %, counts, guidance, etc.).",
            "",
            "Rules: Use ONLY facts and numbers present in the source. Do not invent or estimate.",
            "If the document has tables, read them carefully. Plain text only, no markdown.",
        ]
    )


def parse_business_update_response(text: str) -> tuple[str, list[str]]:
    summary = ""
    highlights: list[str] = []
    body = text.strip()
    if "HIGHLIGHTS:" in body:
        pre, post = body.split("HIGHLIGHTS:", 1)
        summary = pre.replace("SUMMARY:", "").strip()
        for line in post.strip().splitlines():
            line = line.strip()
            if line.startswith("- "):
                highlights.append(line[2:].strip())
            elif line.startswith("•"):
                highlights.append(line.lstrip("•").strip())
            elif line and not line.upper().startswith(("HIGHLIGHTS", "SUMMARY", "OUTLOOK")):
                highlights.append(line)
    else:
        summary = body.replace("SUMMARY:", "").strip()
    return summary, highlights


def summarize_business_update(record: dict, client: GeminiClient | None = None) -> dict:
    from gemini_client import GeminiClient

    pdf_kind, source_text = build_source_document(record)
    if not source_text:
        source_text = (record.get("subject") or "").strip()

    prompt = build_business_update_prompt(record, source_text)
    system = (
        "You are a senior equity research analyst covering NSE-listed Indian companies. "
        "Produce detailed, number-rich summaries from business update filings. "
        "Never fabricate metrics. Plain text only."
    )
    gemini = client or GeminiClient()
    text = gemini.generate(
        prompt,
        system=system,
        temperature=0.15,
        max_output_tokens=2048,
    )
    summary, highlights = parse_business_update_response(text)
    return {
        "summary": summary or None,
        "highlights": highlights,
        "source_kind": pdf_kind,
        "source_chars": len(source_text),
    }
