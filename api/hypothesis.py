"""Parallel-hypothesis generation via a direct model call.

INTERIM IMPLEMENTATION. The human enters a research goal; we ask the model for
a few *parallel* competing hypotheses. The human either selects one or picks one
and says what to do differently, and we generate a fresh parallel set derived
from that choice. No tree, no tournament — just rounds of parallel candidates
the human steers.

TODO(H1): swap this out for the full Google AI co-scientist protocol
(generate → reflect → rank via Elo tournament → evolve → meta-review). The REST
+ event contract and data model for that are pinned in
docs/plans/H1-hypothesis-coscientist.md; this module is the placeholder that
lets the UI ship against real generations now.

Model note: the user asked for Fable. Fable's safety classifiers frequently
refuse benign life-sciences / chemistry content, which a science co-scientist
hits constantly — the Fable → Opus fallback lives in api/llm.py (shared with
the O2 advisor). The response records which model actually served the set.
"""

from __future__ import annotations

from . import llm


HYPOTHESIS_MODEL = llm.PRIMARY_MODEL
HYPOTHESIS_FALLBACK_MODEL = llm.FALLBACK_MODEL
_MAX_TOKENS = 4000  # 6 hypotheses with rationale can exceed 2000 and truncate mid-JSON


def _system() -> str:
    return (
        "You are a scientific hypothesis generator for a human researcher. Given a "
        "research goal, propose DISTINCT, competing hypotheses that attack the goal "
        "from genuinely different angles (different mechanisms, assumptions, or "
        "levels of analysis) — not variations on one idea. Each hypothesis must be "
        "specific and testable.\n\n"
        "Respond with ONLY a JSON array (no prose, no markdown fences). Each element "
        'is an object with exactly these keys: "statement" (one clear sentence stating '
        'the hypothesis) and "rationale" (1-2 sentences on the reasoning and how it '
        "could be tested)."
    )


def _user(
    goal: str, parent: dict | None, feedback: str | None, n: int, context: str | None = None
) -> str:
    # O1 onboarding: the goal may come with the rest of the problem brief
    # (significance, prior work, data, evaluation protocol). Give the generator
    # that context so the candidates are grounded in this problem.
    ctx_block = (
        f"\n\nContext from the researcher's problem brief (respect the data, the evaluation protocol and the constraints):\n{context.strip()}"
        if context and context.strip()
        else ""
    )
    if parent is None:
        return f"Research goal: {goal}{ctx_block}\n\nGenerate {n} distinct, competing hypotheses."
    lines = [
        f"Research goal: {goal}{ctx_block}",
        "",
        "The researcher chose to build on this hypothesis:",
        f"- {parent.get('statement', '')}",
    ]
    if parent.get("rationale"):
        lines.append(f"  (rationale: {parent['rationale']})")
    if feedback and feedback.strip():
        lines += ["", f"They want the next set to account for this direction: {feedback.strip()}"]
    lines += [
        "",
        f"Generate {n} new distinct hypotheses that descend from the chosen one — "
        "refine it, combine it with a complementary idea, or push it further — while "
        "staying genuinely different from each other.",
    ]
    return "\n".join(lines)


def _parse(text: str, n: int) -> list[dict]:
    """Extract a JSON array of {statement, rationale} from the model text,
    tolerating stray prose or code fences."""
    out: list[dict] = []
    for item in llm.parse_json_array(text):
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        if not statement:
            continue
        out.append({"statement": statement, "rationale": str(item.get("rationale", "")).strip()})
    return out[:n]


async def generate(
    goal: str,
    parent: dict | None = None,
    feedback: str | None = None,
    n: int = 4,
    context: str | None = None,
) -> tuple[list[dict], str]:
    """Generate `n` parallel hypotheses. Returns (hypotheses, served_by_model).

    Tries the Fable model first; on a refusal/empty/error, retries on the
    fallback model so science content (which Fable often declines) still works.
    `context` (optional) is the rest of the problem brief when the search is
    seeded from onboarding (O1)."""
    system, user = _system(), _user(goal, parent, feedback, n, context)
    res = await llm.complete(system, user, max_tokens=_MAX_TOKENS)
    # Both models failed → empty list so the caller reports a clean 502 rather
    # than propagating an unhandled 500.
    return _parse(res.text, n), res.served_by
