"""Thin wrapper over OpenAI's Deep Research API (Responses API, background mode).

Kept deliberately small and side-effect-free at import time: the ``openai``
SDK is imported lazily inside functions so the tool module imports (and the v0
smoke test passes) even when the package or key is absent. All network calls run
in a worker thread via ``asyncio.to_thread`` so they never block the event loop.

OpenAI deep-research models (``o3-deep-research`` / ``o4-mini-deep-research``)
require at least one data-source tool; we always attach ``web_search_preview``.
Runs are submitted with ``background=True`` and polled with ``responses.retrieve``,
so a job outlives the per-turn supervisor subprocess.
"""
from __future__ import annotations

import asyncio
from typing import Any

from scaffold import settings
from . import store

# Import the OpenAI SDK at MODULE LOAD (process startup) — NOT lazily inside the
# tool handler. The research runtime executes MCP tool calls under an isolation
# layer that masks site-packages: a fresh ``import openai`` at call time raises
# ModuleNotFoundError, but modules already in ``sys.modules`` from startup
# survive (memory: runtime-sandbox-strips-deps). tools_registry imports this
# module while building the supervisor's servers at startup, so importing here
# caches ``OpenAI`` before the mask takes effect. Guarded so a host/env without
# the SDK still imports the module (the tool then reports itself unavailable).
try:
    from openai import OpenAI as _OpenAI  # noqa: N812
    _IMPORT_ERR: Exception | None = None
except Exception as _e:  # pragma: no cover - only when dep missing
    _OpenAI = None
    _IMPORT_ERR = _e


class DeepResearchUnavailable(RuntimeError):
    """Raised when the tool can't run — missing key or missing ``openai`` SDK.
    Carries a human-actionable message the agent can relay verbatim."""


def _client():
    """Build an OpenAI client or raise DeepResearchUnavailable with guidance."""
    if not settings.OPENAI_API_KEY:
        raise DeepResearchUnavailable(
            "deep_research is not configured: no OPENAI_API_KEY. Ask the human to "
            "add OPENAI_API_KEY=sk-... to .env (and restart the API) to enable "
            "external deep research. Until then, proceed without it."
        )
    if _OpenAI is None:
        raise DeepResearchUnavailable(
            f"the 'openai' package failed to import ({_IMPORT_ERR}). It is a "
            "declared dependency — ask the human to rebuild the image so it is "
            "importable at runtime startup."
        )
    kwargs: dict[str, Any] = {"api_key": settings.OPENAI_API_KEY}
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return _OpenAI(**kwargs)


# OpenAI Responses statuses → our durable handle states.
_STATE_MAP = {
    "queued": store.RUNNING,
    "in_progress": store.RUNNING,
    "completed": store.COMPLETED,
    "failed": store.FAILED,
    "incomplete": store.FAILED,
    "cancelled": store.CANCELLED,
    "canceled": store.CANCELLED,
}


def _map_state(openai_status: str | None) -> str:
    return _STATE_MAP.get(openai_status or "", store.RUNNING)


async def submit(query: str, instructions: str, model: str) -> str:
    """Kick off a background deep-research run; return the OpenAI response id."""
    client = _client()
    dev_text = instructions.strip() or (
        "You are a rigorous research assistant for a working scientist. Produce a "
        "well-structured, citation-backed briefing that scopes the problem, "
        "surveys prior work and the current landscape, flags key uncertainties, "
        "and lists concrete leads. Prefer primary sources."
    )

    def _do():
        resp = client.responses.create(
            model=model,
            background=True,
            input=[
                {"role": "developer",
                 "content": [{"type": "input_text", "text": dev_text}]},
                {"role": "user",
                 "content": [{"type": "input_text", "text": query}]},
            ],
            tools=[{"type": "web_search_preview"}],
        )
        return resp.id

    return await asyncio.to_thread(_do)


async def poll(response_id: str) -> tuple[str, Any]:
    """Retrieve a background response; return ``(mapped_state, raw_response)``."""
    client = _client()

    def _do():
        resp = client.responses.retrieve(response_id)
        return _map_state(getattr(resp, "status", None)), resp

    return await asyncio.to_thread(_do)


async def cancel(response_id: str) -> None:
    client = _client()

    def _do():
        try:
            client.responses.cancel(response_id)
        except Exception:
            pass  # best-effort; already-terminal responses can't be cancelled

    await asyncio.to_thread(_do)


def extract_report(resp: Any) -> tuple[str, list[str]]:
    """Pull the final report text + deduped citation URLs from a completed response.

    ``output_text`` is the SDK's flattened convenience accessor; citation URLs
    live in ``url_citation`` annotations on the output message content parts.
    Both are read defensively so an API shape change degrades rather than crashes.
    """
    text = getattr(resp, "output_text", None) or ""
    citations: list[str] = []
    try:
        for item in (getattr(resp, "output", None) or []):
            for part in (getattr(item, "content", None) or []):
                for ann in (getattr(part, "annotations", None) or []):
                    url = getattr(ann, "url", None)
                    if url and url not in citations:
                        citations.append(url)
    except Exception:
        pass
    if not text:
        # Fall back to concatenating any output_text parts we can find.
        try:
            chunks = []
            for item in (getattr(resp, "output", None) or []):
                for part in (getattr(item, "content", None) or []):
                    t = getattr(part, "text", None)
                    if t:
                        chunks.append(t)
            text = "\n\n".join(chunks)
        except Exception:
            text = ""
    return text, citations
