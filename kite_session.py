"""
One-shot Zerodha Kite Connect session helper.

Flow (official):
1. Create an app at https://developers.kite.trade/apps — note API key & secret.
2. Set redirect URL in the app (e.g. http://127.0.0.1/) to match where the browser lands after login.
3. Run:  python kite_session.py
4. Open the printed login URL, log in, approve; copy request_token from the
   redirect URL query string (…?request_token=…&action=…).
5. Paste request_token when prompted — the script prints access_token; add it
   to project .env as KITE_ACCESS_TOKEN=... (or export in shell; never commit .env).

Access tokens expire daily (until ~next morning IST per Kite docs) — regenerate
when you see TokenException / 403 on API calls.
"""
import os
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.environ.get("KITE_API_KEY", "").strip()
API_SECRET = os.environ.get("KITE_API_SECRET", "").strip()
# Redirect URL is configured in https://developers.kite.trade/apps — it must match
# where Zerodha sends you after login (e.g. http://127.0.0.1/).


def main() -> None:
    if not API_KEY or not API_SECRET:
        print(
            "Set KITE_API_KEY and KITE_API_SECRET in the environment, then re-run.\n"
            "Example: export KITE_API_KEY=... && export KITE_API_SECRET=..."
        )
        return

    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()
    print("Open this URL in a browser (if it did not open automatically):\n")
    print(login_url)
    print()
    try:
        webbrowser.open(login_url)
    except Exception:
        pass

    raw = input(
        "After login, paste the request_token from the redirect URL "
        "(only the token value, not the full URL): "
    ).strip()
    if not raw:
        print("No token pasted; exiting.")
        return
    # User might paste full URL
    if "request_token=" in raw:
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(raw).query)
        tokens = q.get("request_token", [])
        request_token = tokens[0] if tokens else ""
    else:
        request_token = raw

    if not request_token:
        print("Could not parse request_token; exiting.")
        return

    try:
        sess = kite.generate_session(request_token, api_secret=API_SECRET)
    except Exception as e:
        print("generate_session failed:", type(e).__name__, e)
        print(
            "\nTypical causes: wrong API_SECRET, request_token already used or expired, "
            "or request_token from a different API_KEY."
        )
        return

    access = sess.get("access_token", "")
    print("\nSuccess. Set this for your scanner (same shell / .env):\n")
    print(f'  export KITE_ACCESS_TOKEN="{access}"')
    print("\nOr add to a .env file loaded by your tooling (never commit .env).")


if __name__ == "__main__":
    main()
