"""Thin wrapper around the MiniMax Anthropic-compatible messages endpoint.

All four modules that call MiniMax (analyse, fetch, panel_digest,
entity_digest) share the same HTTP shape (URL + auth headers + response
extraction). They differ on system prompt, max_tokens, timeout, and retry
policy — so the helper only abstracts the HTTP call itself; retries stay
in each caller where the backoff/budget semantics live.
"""
import os

import aiohttp

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL   = os.getenv("MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_URL     = "https://api.minimax.io/anthropic/v1/messages"

_RETRY_ERR_TYPES = {"overloaded_error", "rate_limit_error", "api_error"}


def should_retry(err: dict, status: int) -> bool:
    return err.get("type") in _RETRY_ERR_TYPES or status == 429 or status >= 500


async def post_messages(
    session:    aiohttp.ClientSession,
    *,
    system:     str,
    user_text:  str,
    max_tokens: int,
    timeout:    float,
    connect:    float = 20,
) -> tuple[str, dict, int]:
    """POST a single user message. Returns (raw_text, error_dict, http_status).

    Per-request timeout is required — aiohttp drops the session default when
    a request-level timeout is set, so omitting `connect` would silently
    uncap the connect phase.
    """
    async with session.post(
        MINIMAX_URL,
        headers={
            "x-api-key":         MINIMAX_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        json={
            "model":      MINIMAX_MODEL,
            "max_tokens": max_tokens,
            "system":     system,
            "messages":   [{"role": "user", "content": user_text}],
        },
        timeout=aiohttp.ClientTimeout(total=timeout, connect=connect),
    ) as resp:
        status = resp.status
        data   = await resp.json(content_type=None)
    err    = data.get("error") or {}
    blocks = data.get("content") or []
    raw    = next(
        (b.get("text", "").strip() for b in blocks if b.get("type") == "text"),
        ""
    )
    return raw, err, status
