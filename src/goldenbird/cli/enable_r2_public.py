"""Enable R2.dev public access + CORS for browser reads of ``ui/*``.

Requires a Cloudflare **API token** (not the R2 S3 access key) with permission
``Account.Workers R2 Storage:Edit`` (or broader Account R2 edit).

Env
---
``CLOUDFLARE_API_TOKEN`` or ``CF_API_TOKEN``
``R2_ACCOUNT_ID``
``R2_BUCKET``

Usage
-----
::

    python -m goldenbird.cli.enable_r2_public
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

from goldenbird.config import ROOT, get_settings

API = "https://api.cloudflare.com/client/v4"


def _token() -> str:
    load_dotenv(ROOT / ".env")
    token = (os.getenv("CLOUDFLARE_API_TOKEN") or os.getenv("CF_API_TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "Missing CLOUDFLARE_API_TOKEN in .env\n"
            "Create one: Cloudflare Dashboard → My Profile → API Tokens → Create Token\n"
            "Template: 'Edit Cloudflare Workers' or custom with Account → Workers R2 Storage → Edit"
        )
    return token


def _request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cloudflare API {method} {path} failed: {exc.code} {body}") from exc


def main() -> int:
    token = _token()
    settings = get_settings()
    account = settings.r2_account_id
    bucket = settings.r2_bucket
    if not account or not bucket:
        raise SystemExit("R2_ACCOUNT_ID and R2_BUCKET required")

    # Enable managed r2.dev subdomain
    enabled = _request(
        "PUT",
        f"/accounts/{account}/r2/buckets/{bucket}/domains/managed",
        token,
        {"enabled": True},
    )
    domain = (enabled.get("result") or {}).get("domain") or ""
    print("managed domain:", domain or enabled)

    # CORS so github.io can fetch JSON
    cors = {
        "rules": [
            {
                "allowed": {
                    "origins": [
                        "https://rakeshkhatri1.github.io",
                        "http://127.0.0.1:8081",
                        "http://localhost:8081",
                    ],
                    "methods": ["GET", "HEAD"],
                    "headers": ["*"],
                },
                "maxAgeSeconds": 86400,
            }
        ]
    }
    _request(
        "PUT",
        f"/accounts/{account}/r2/buckets/{bucket}/cors",
        token,
        cors,
    )
    print("CORS: OK for github.io + localhost")

    if domain:
        base = f"https://{domain}/ui"
        print("DATA_BASE_URL=" + base)
        print("Set that in config.js / .env and redeploy Pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
