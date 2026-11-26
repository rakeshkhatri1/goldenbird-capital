#!/usr/bin/env python3
"""
Local Gemini proxy for index.html (keeps API keys off the browser).

  python3 gemini_server.py
  # default http://127.0.0.1:8787

Endpoints:
  GET  /api/gemini/health
  POST /api/gemini/test          { "prompt": "..." }
  POST /api/gemini/rank-preview  { "sector", "timeframe", "stocks": [...] }
  POST /api/gemini/business-update  { "symbol", "name", "desc", "subject", "sort_date" }
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from gemini_client import GeminiClient, GeminiError
from business_update_utils import summarize_business_update

HOST = os.environ.get("GEMINI_SERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("GEMINI_SERVER_PORT", "8787"))
ALLOW_ORIGIN = os.environ.get("GEMINI_CORS_ORIGIN", "*")

_client: GeminiClient | None = None


def get_client() -> GeminiClient:
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON body: {e}") from e


def _rank_preview_prompt(sector: str, timeframe: str, stocks: list[dict]) -> str:
    lines = [
        f"Sector / peer group: {sector}",
        f"Timeframe: {timeframe}",
        "",
        "Stocks (JSON):",
        json.dumps(stocks, indent=2),
        "",
        "Task: Rank these Indian equities for 6–12 month risk-adjusted opportunity.",
        "Use only the data provided. Be concise.",
        "Format:",
        "1. SYMBOL — score /10 — one-line reason",
        "2. ...",
        "Then 2–3 bullet risks for the top pick.",
    ]
    return "\n".join(lines)


class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/gemini/health":
            keys = load_key_count()
            from gemini_client import load_model_chain

            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "keys_loaded": keys,
                    "models": load_model_chain(),
                },
            )
            return
        _json_response(self, 404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = _read_json(self)
        except ValueError as e:
            _json_response(self, 400, {"ok": False, "error": str(e)})
            return

        try:
            client = get_client()
            if path == "/api/gemini/test":
                prompt = (data.get("prompt") or "").strip()
                if not prompt:
                    _json_response(self, 400, {"ok": False, "error": "prompt required"})
                    return
                text = client.generate(prompt)
                _json_response(self, 200, {"ok": True, "text": text})
                return

            if path == "/api/gemini/business-update":
                symbol = (data.get("symbol") or "").strip().upper()
                subject = (data.get("subject") or "").strip()
                if not symbol or not subject:
                    _json_response(
                        self, 400, {"ok": False, "error": "symbol and subject required"}
                    )
                    return
                result = summarize_business_update(data, client=client)
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "summary": result.get("summary"),
                        "highlights": result.get("highlights") or [],
                        "source_kind": result.get("source_kind"),
                        "source_chars": result.get("source_chars"),
                        "model": client.last_model_used,
                    },
                )
                return

            if path == "/api/gemini/rank-preview":
                sector = (data.get("sector") or "Unknown sector").strip()
                timeframe = (data.get("timeframe") or "weekly").strip()
                stocks = data.get("stocks") or []
                if not stocks:
                    _json_response(self, 400, {"ok": False, "error": "stocks array required"})
                    return
                prompt = _rank_preview_prompt(sector, timeframe, stocks[:12])
                system = (
                    "You are an equity research assistant for Indian markets. "
                    "Output plain text only, no markdown fences."
                )
                text = client.generate(prompt, system=system)
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "text": text,
                        "sector": sector,
                        "count": len(stocks),
                        "model": client.last_model_used,
                    },
                )
                return

            _json_response(self, 404, {"ok": False, "error": "Not found"})
        except GeminiError as e:
            _json_response(self, 502, {"ok": False, "error": str(e)})
        except Exception as e:
            _json_response(self, 500, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def load_key_count() -> int:
    from gemini_client import load_api_keys

    return len(load_api_keys())


def main() -> None:
    if load_key_count() == 0:
        print("Missing GEMINI_API_KEY(S) in .env", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), GeminiHandler)
    print(f"Gemini test server → http://{HOST}:{PORT}")
    print("  GET  /api/gemini/health")
    print("  POST /api/gemini/test")
    print("  POST /api/gemini/rank-preview")
    print("  POST /api/gemini/business-update")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
