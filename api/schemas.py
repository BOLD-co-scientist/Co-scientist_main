from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StartResearchRequest(BaseModel):
    task: str
    session_id: str | None = None


class StartResearchResponse(BaseModel):
    session_id: str
    task: str


class HumanDirective(BaseModel):
    text: str


class StartEvolutionRequest(BaseModel):
    command: str
    session_id: str | None = None


class StartEvolutionResponse(BaseModel):
    session_id: str
    command: str


class HitlAnswer(BaseModel):
    decision: str  # "approve" | "reject"
    note: str | None = None


class RoleSummary(BaseModel):
    name: str
    description: str
    tools: list[str]
    can_spawn: bool
    model: str


class MemorySearchResult(BaseModel):
    layer: str
    hits: list[dict[str, Any]]
