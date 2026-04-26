"""Loads role YAML files and renders prompts with placeholder substitution."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import settings


@dataclass
class RoleConfig:
    name: str
    model: str
    system_prompt_path: str
    tools: list[str] = field(default_factory=list)
    can_spawn: bool = False
    hitl_level: str = "default"  # "default" | "strict" | "open"
    description: str = ""

    @property
    def prompt_path(self) -> Path:
        p = Path(self.system_prompt_path)
        return p if p.is_absolute() else settings.ROOT / p


def load_role(path: Path) -> RoleConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RoleConfig(
        name=raw["name"],
        model=raw.get("model", settings.MODEL_SUBAGENT),
        system_prompt_path=raw["system_prompt_path"],
        tools=list(raw.get("tools", [])),
        can_spawn=bool(raw.get("can_spawn", False)),
        hitl_level=raw.get("hitl_level", "default"),
        description=raw.get("description", ""),
    )


def load_supervisor() -> RoleConfig:
    return load_role(settings.ROLES / "supervisor.yaml")


def list_subagent_roles() -> list[RoleConfig]:
    out = []
    sub_dir = settings.ROLES / "subagents"
    if not sub_dir.exists():
        return out
    for p in sorted(sub_dir.glob("*.yaml")):
        out.append(load_role(p))
    return out


_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, **values: str) -> str:
    """Replace {{KEY}} with values[KEY]; leave unknown placeholders intact."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(values.get(key, m.group(0)))
    return _PLACEHOLDER.sub(sub, template)


def render_prompt(role: RoleConfig, **values: str) -> str:
    text = role.prompt_path.read_text(encoding="utf-8")
    return render(text, ROLE_NAME=role.name, **values)
