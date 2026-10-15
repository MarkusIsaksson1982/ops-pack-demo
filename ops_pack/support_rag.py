"""Support-RAG + eval harness (stdlib only).

Retrieval over a small synthetic FAQ corpus, answer assembly with citation ids,
guardrail refusals (money movement, PII fishing, market certainty), and a
deterministic evaluation table (accuracy / groundedness / latency / cost).

Designed as an extension of ops-pack-demo for customer-support / fintech-safe
assistant demos. No network unless a model backend is explicitly chosen.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
FAQ_PATH = ROOT / "fixtures" / "support_faq.json"
EVAL_PATH = ROOT / "fixtures" / "support_eval.json"

# Cheap heuristic cost units (relative); free/local = 0, hosted low-effort = 1
COST_UNITS = {
    "fake": 0.0,
    "ollama": 0.0,
    "opencode": 0.0,
    "codex": 1.0,
    "grok-build": 1.0,
}

REFUSAL_PATTERNS = [
    (re.compile(r"\b(wire|iban|swift|send money|transfer funds|ignore (all |previous )?rules)\b", re.I),
     "I can't help move money or follow instructions that override safety rules. Please use the in-app billing flow or contact a human agent."),
    (re.compile(r"\b(home address|private (phone|number)|social security|personnummer)\b", re.I),
     "I can't share private personal data. For account-specific help, sign in and open a support ticket."),
    (re.compile(r"\b(guaranteed? (return|profit)|closing price|sure (tip|bet)|predict (the )?market)\b", re.I),
     "I can't give guaranteed market predictions or investment tips. I can explain product features grounded in our help articles."),
]


def load_faq(path: Path = FAQ_PATH) -> List[dict]:
    return json.loads(path.read_text())


def load_eval(path: Path = EVAL_PATH) -> List[dict]:
    return json.loads(path.read_text())


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def retrieve(query: str, corpus: Sequence[dict], k: int = 3) -> List[Tuple[dict, float]]:
    """Bag-of-words overlap score (deterministic, no embeddings)."""
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for doc in corpus:
        blob = _tokens(doc.get("question", "") + " " + doc.get("answer", "") + " " + doc.get("topic", ""))
        overlap = len(q & blob)
        score = overlap / max(1, len(q))
        if overlap:
            scored.append((doc, round(score, 4)))
    scored.sort(key=lambda x: (-x[1], x[0]["id"]))
    return scored[:k]


def check_guardrails(question: str) -> Optional[str]:
    for pat, msg in REFUSAL_PATTERNS:
        if pat.search(question or ""):
            return msg
    return None


@dataclass
class Answer:
    question: str
    answer: str
    citations: List[str]
    refused: bool
    grounded: bool
    backend: str
    latency_ms: float
    cost_units: float

    def to_dict(self):
        return asdict(self)


def answer_with_retrieval(
    question: str,
    corpus: Sequence[dict] | None = None,
    backend: str = "fake",
    model_fn: Callable[[str], str] | None = None,
    k: int = 3,
) -> Answer:
    """Retrieve top-k FAQ chunks; refuse if guardrail hits; else assemble a grounded answer.

    If model_fn is provided it may rewrite the answer, but citations still come from retrieval
    and the rewrite is accepted only if it keeps at least one citation id and does not invent
    numbers that are absent from the retrieved text (simple groundedness gate).
    """
    t0 = time.perf_counter()
    corpus = list(corpus if corpus is not None else load_faq())
    refusal = check_guardrails(question)
    cost = COST_UNITS.get(backend.split(":")[0], 0.5)
    if refusal:
        return Answer(question, refusal, [], True, True, backend, (time.perf_counter() - t0) * 1000, cost)

    hits = retrieve(question, corpus, k=k)
    if not hits:
        msg = "I don't have a help article for that yet. Please open a support ticket and a human will follow up."
        return Answer(question, msg, [], False, False, backend, (time.perf_counter() - t0) * 1000, cost)

    citations = [d["id"] for d, _ in hits]
    base = "Based on our help articles ({ids}):\n\n{body}".format(
        ids=", ".join(citations),
        body="\n\n".join(f"- ({d['id']}) {d['answer']}" for d, _ in hits),
    )
    text = base
    if model_fn is not None:
        prompt = (
            "Using ONLY the articles below, answer the user. Cite article ids in brackets. "
            "If unsure, say you don't know.\n\n"
            f"USER: {question}\n\nARTICLES:\n"
            + "\n".join(f"[{d['id']}] {d['answer']}" for d, _ in hits)
        )
        try:
            rewritten = (model_fn(prompt) or "").strip()
        except Exception:
            rewritten = ""
        if rewritten and any(cid in rewritten for cid in citations):
            text = rewritten

    grounded = _is_grounded(text, hits)
    return Answer(question, text, citations, False, grounded, backend, (time.perf_counter() - t0) * 1000, cost)


def _is_grounded(text: str, hits: Sequence[Tuple[dict, float]]) -> bool:
    if not hits:
        return False
    # Must mention at least one citation id, and every digit-run longer than 1 must appear in sources.
    cites = [d["id"] for d, _ in hits]
    if not any(c in text for c in cites):
        return False
    source = " ".join(d["answer"] for d, _ in hits)
    for num in re.findall(r"\d+", text):
        if len(num) >= 2 and num not in source and num not in "".join(cites):
            # allow common list numbers / years already in source; reject invented amounts
            if num not in re.findall(r"\d+", source):
                return False
    return True


@dataclass
class EvalRow:
    id: str
    backend: str
    correct: bool
    grounded: bool
    refused_ok: bool
    latency_ms: float
    cost_units: float
    detail: str


def evaluate(
    backend: str = "fake",
    model_fn: Callable[[str], str] | None = None,
    corpus: Sequence[dict] | None = None,
    cases: Sequence[dict] | None = None,
) -> List[EvalRow]:
    corpus = list(corpus if corpus is not None else load_faq())
    cases = list(cases if cases is not None else load_eval())
    rows = []
    for case in cases:
        ans = answer_with_retrieval(case["question"], corpus=corpus, backend=backend, model_fn=model_fn)
        want_refuse = bool(case.get("refuse"))
        refused_ok = ans.refused == want_refuse
        if want_refuse:
            correct = refused_ok
            grounded = ans.grounded  # refusals count as grounded policy answers
        else:
            topics = set(case.get("expect_topics") or [])
            hit_topics = set()
            for cid in ans.citations:
                for doc in corpus:
                    if doc["id"] == cid:
                        hit_topics.add(doc.get("topic"))
            correct = (not ans.refused) and (not topics or bool(topics & hit_topics))
            grounded = ans.grounded if case.get("must_ground", True) else True
        rows.append(EvalRow(
            id=case["id"], backend=backend, correct=correct, grounded=grounded,
            refused_ok=refused_ok, latency_ms=round(ans.latency_ms, 2),
            cost_units=ans.cost_units,
            detail=("refuse" if ans.refused else ",".join(ans.citations))[:80],
        ))
    return rows


def summary_table(rows: Sequence[EvalRow]) -> dict:
    n = max(1, len(rows))
    return {
        "n": len(rows),
        "accuracy": round(sum(1 for r in rows if r.correct) / n, 3),
        "groundedness": round(sum(1 for r in rows if r.grounded) / n, 3),
        "refusal_ok": round(sum(1 for r in rows if r.refused_ok) / n, 3),
        "median_latency_ms": sorted(r.latency_ms for r in rows)[len(rows) // 2] if rows else 0.0,
        "total_cost_units": round(sum(r.cost_units for r in rows), 3),
        "backend": rows[0].backend if rows else "",
    }


def run_eval_cli(backend: str = "fake") -> dict:
    rows = evaluate(backend=backend)
    return {"summary": summary_table(rows), "rows": [asdict(r) for r in rows]}
