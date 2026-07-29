"""Prompt-caching demo (stdlib only — no SDK, no `ant`, no pip install).

Run:
    set ANTHROPIC_API_KEY=sk-ant-...        (PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-...")
    python _cc_prompt_cache_demo.py

It fires two identical requests against claude-opus-4-8 with a large, stable
cached system prefix (>4096 tokens, the Opus minimum). Expected:
    request 1 -> cache_creation_input_tokens > 0   (cache written)
    request 2 -> cache_read_input_tokens   > 0     (cache hit, ~0.1x cost)
"""

import json
import os
import sys
import urllib.request

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    sys.exit("Set ANTHROPIC_API_KEY first.")

# Stable instruction block, repeated to clear the 4096-token Opus minimum.
# (~24k chars ~= 6k tokens — comfortably above the cache floor.)
_BLOCK = (
    "You are an AI assistant tasked with analyzing literary works. Provide "
    "insightful, well-structured commentary on themes, characters, narrative "
    "structure, and writing style. Ground every claim in the text. "
)
SYSTEM_PREFIX = _BLOCK * 120  # large + byte-identical across requests = cacheable

URL = "https://api.anthropic.com/v1/messages"
HEADERS = {
    "content-type": "application/json",
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
}


def call(question: str) -> dict:
    body = {
        "model": "claude-opus-4-8",
        "max_tokens": 256,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PREFIX,
                "cache_control": {"type": "ephemeral"},  # breakpoint on the stable prefix
            }
        ],
        "messages": [{"role": "user", "content": question}],  # volatile part, uncached
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers=HEADERS, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def show(label: str, usage: dict) -> None:
    print(
        f"{label}: created={usage.get('cache_creation_input_tokens')} "
        f"read={usage.get('cache_read_input_tokens')} "
        f"uncached_input={usage.get('input_tokens')}"
    )


# Send request 1, wait for it to return (cache is readable once it completes),
# then send the identical request 2.
show("request 1 (write)", call("Analyze the major themes in Pride and Prejudice.")["usage"])
show("request 2 (hit)  ", call("Analyze the major themes in Pride and Prejudice.")["usage"])
