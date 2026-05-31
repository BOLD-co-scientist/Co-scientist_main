"""Three-layer JSONL memory. v0 uses BM25 for retrieval; the evolution agent
can graft on embeddings later."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Iterator
from datetime import datetime

from rank_bm25 import BM25Okapi

from . import settings


_TOKEN = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _iter(path: Path) -> Iterator[dict]:
    if not path.exists():
        return iter(())
    def gen():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    return gen()


def _recall(path: Path, query: str, k: int) -> list[dict]:
    records = list(_iter(path))
    if not records:
        return []
    corpus = [_tokenize(r.get("text", "")) for r in records]
    q_tokens = _tokenize(query)
    if not q_tokens:
        return records[-k:]
    # BM25 is unreliable for very small corpora — fall back to token-overlap
    # ranking until we have enough docs.
    if len(records) < 5:
        q_set = set(q_tokens)
        ranked = sorted(
            ((sum(1 for t in c if t in q_set), i) for i, c in enumerate(corpus)),
            reverse=True,
        )
        return [records[i] for score, i in ranked[:k] if score > 0]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(q_tokens)
    order = sorted(range(len(records)), key=lambda i: scores[i], reverse=True)
    return [records[i] for i in order[:k] if scores[i] > 0]


# ---------- public API ----------

def remember_agent(session_id: str, agent_id: str, text: str, **tags) -> str:
    rid = uuid.uuid4().hex[:12]
    rec = {"id": rid, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "agent": agent_id, "text": text, "tags": tags}
    _append(settings.session_dir(session_id) / "memory" / "agent" / f"{agent_id}.jsonl", rec)
    return rid


def remember_project(session_id: str, agent_id: str, text: str, **tags) -> str:
    rid = uuid.uuid4().hex[:12]
    rec = {"id": rid, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "agent": agent_id, "text": text, "tags": tags}
    _append(settings.session_dir(session_id) / "memory" / "project" / "shared.jsonl", rec)
    return rid


def remember_global(text: str, source: str, **tags) -> str:
    rid = uuid.uuid4().hex[:12]
    rec = {"id": rid, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": source, "text": text, "tags": tags}
    _append(settings.GLOBAL_MEMORY, rec)
    return rid


def recall_agent(session_id: str, agent_id: str, query: str, k: int = 5) -> list[dict]:
    return _recall(settings.session_dir(session_id) / "memory" / "agent" / f"{agent_id}.jsonl", query, k)


def recall_project(session_id: str, query: str, k: int = 5) -> list[dict]:
    return _recall(settings.session_dir(session_id) / "memory" / "project" / "shared.jsonl", query, k)


def recall_global(query: str, k: int = 5) -> list[dict]:
    return _recall(settings.GLOBAL_MEMORY, query, k)
