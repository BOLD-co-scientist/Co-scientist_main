"""Deep-research MCP tool (R14).

Wraps OpenAI's Deep Research API (Responses API, background mode) as a
durable async job — mirroring the R12 long-job runner so a report survives the
per-turn supervisor subprocess. See docs/plans/R14-deep-research.md.
"""
