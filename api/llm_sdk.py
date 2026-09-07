"""Platform model call with a Claude Agent SDK fallback.

Some deployments (this host, 2026-09-07) hold an Anthropic credential that the
Claude Code CLI accepts but the direct Messages API refuses with 403 "Request
not allowed". ``api/llm.py::complete`` covers the direct path (Fable → Opus,
client-side fallback); this wrapper tries it first and, when BOTH direct models
fail, re-runs the same system + user prompt through the Claude Agent SDK — the
path the research/evolution runtimes and the R16 reflection runner already use
— with no tools and a single turn. ``served_by`` records ``sdk:<model>`` so
every record shows which path answered.

Knob: ``COSCIENTIST_PLATFORM_SDK_FALLBACK`` = "1" (default: direct first, SDK on
failure) | "0" (direct only) | "only" (skip the direct API, always the SDK).
"""

from __future__ import annotations

import os

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from scaffold import settings

from . import llm

SDK_FALLBACK = os.environ.get("COSCIENTIST_PLATFORM_SDK_FALLBACK", "1").strip().lower()
SDK_MODEL = os.environ.get("COSCIENTIST_PLATFORM_SDK_MODEL", settings.MODEL_SUPERVISOR)
SDK_FALLBACK_MODEL = os.environ.get("COSCIENTIST_PLATFORM_SDK_FALLBACK_MODEL", settings.MODEL_REFLECT)


async def complete_via_sdk(system: str, user: str, *, model: str | None = None, api_key: str | None = None) -> llm.LLMResult:
    """One single-turn, tool-less Agent SDK call. Never raises."""
    model = model or SDK_MODEL
    env = {"ANTHROPIC_API_KEY": api_key} if api_key else {}
    options = ClaudeAgentOptions(system_prompt=system, model=model, allowed_tools=[], max_turns=1, cwd=str(settings.ROOT), env=env)
    out: list[str] = []
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            out.append(block.text)
    except Exception as e:  # noqa: BLE001
        return llm.LLMResult(text="", served_by=f"sdk:{model}", attempts=[{"model": f"sdk:{model}", "outcome": f"error: {e.__class__.__name__}: {str(e)[:200]}"}], error=f"sdk:{model}: {e.__class__.__name__}: {str(e)[:200]}")
    text = "".join(out).strip()
    if not text:
        return llm.LLMResult(text="", served_by=f"sdk:{model}", attempts=[{"model": f"sdk:{model}", "outcome": "empty"}], error=f"sdk:{model}: empty")
    return llm.LLMResult(text=text, served_by=f"sdk:{model}", attempts=[{"model": f"sdk:{model}", "outcome": "ok"}])


async def complete_with_sdk_fallback(system: str, user: str, *, max_tokens: int = 4000, api_key: str | None = None, primary: str | None = None, fallback: str | None = None) -> llm.LLMResult:
    """Direct API (Fable → Opus) first; the Agent SDK when both direct models
    fail (or always, with COSCIENTIST_PLATFORM_SDK_FALLBACK=only). Same
    signature as ``llm.complete`` so callers can swap them."""
    attempts: list[dict] = []
    if SDK_FALLBACK != "only":
        res = await llm.complete(system, user, max_tokens=max_tokens, api_key=api_key, primary=primary, fallback=fallback)
        if res.ok or SDK_FALLBACK == "0":
            return res
        attempts = list(res.attempts)
    for model in (SDK_MODEL, SDK_FALLBACK_MODEL):
        if not model:
            continue
        res2 = await complete_via_sdk(system, user, model=model, api_key=api_key)
        attempts += res2.attempts
        if res2.ok:
            res2.attempts = attempts
            return res2
    return llm.LLMResult(text="", served_by=f"sdk:{SDK_MODEL}", attempts=attempts, error="; ".join(f"{a['model']}: {a['outcome']}" for a in attempts) or "no model configured")
