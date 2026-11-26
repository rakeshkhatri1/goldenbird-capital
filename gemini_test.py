#!/usr/bin/env python3
"""Quick CLI test: python3 gemini_test.py \"Say hello in one sentence.\" """
from __future__ import annotations

import sys

from gemini_client import GeminiClient, GeminiError


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or "Reply with exactly: Gemini OK"
    try:
        client = GeminiClient()
        text = client.generate(prompt, temperature=0.2, max_output_tokens=256)
        if client.last_model_used:
            print(f"(model: {client.last_model_used})", file=sys.stderr)
        print(text)
    except GeminiError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
