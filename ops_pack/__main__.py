"""CLI. Examples:
  python -m ops_pack demo --backend fake
  python -m ops_pack eval --backend ollama:llama3.2:3b --backend opencode:big-pickle --out results.json
  python -m ops_pack approve --db ops.db --id tracker:t3 --approver sam@company.example
  python -m ops_pack dq --db ops.db
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import backends, core

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixtures(db):
    ref = json.loads((FIX / "reference.json").read_text())
    core.load_reference(db, ref["vendors"], ref["people"])
    return json.loads((FIX / "requests.json").read_text())


def cmd_demo(a):
    db = core.connect(a.db)
    reqs = load_fixtures(db)
    out = {"ingest": core.ingest(db, reqs), "enrich": core.enrich(db),
           "route": core.route(db, core.DEFAULT_RULES, backends.get(a.backend[0]) if a.backend else None),
           "dq": core.dq(db, a.at), "requests": core.summary(db)}
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_eval(a):
    items = json.loads((FIX / "eval_free_text.json").read_text())
    results = []
    for spec in a.backend:
        be = backends.get(spec)
        rows = []
        for it in items:
            c = core.classify(it["text"], be)
            rows.append({"ok": c.valid and c.team == it["team"], "valid": c.valid, "team": c.team, "want": it["team"],
                         "latency_s": c.latency_s, "errors": c.errors})
            print(f"{spec:40s} {'OK ' if rows[-1]['ok'] else 'BAD'} valid={c.valid} {c.team}/{it['team']} {c.latency_s}s", file=sys.stderr)
        n = len(rows)
        lat = sorted(r["latency_s"] for r in rows)
        results.append({"backend": spec, "n": n, "accuracy": round(sum(r["ok"] for r in rows) / n, 3),
                        "schema_valid": round(sum(r["valid"] for r in rows) / n, 3), "median_latency_s": lat[n // 2],
                        "date": time.strftime("%Y-%m-%d"), "rows": rows})
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"| {r['backend']} | {r['accuracy']:.0%} | {r['schema_valid']:.0%} | {r['median_latency_s']} s |")


def cmd_approve(a):
    db = core.connect(a.db)
    print(json.dumps(core.decide(db, a.id, a.approver, not a.reject)))


def cmd_dq(a):
    print(json.dumps(core.dq(core.connect(a.db), a.at)))


def main(argv=None):
    p = argparse.ArgumentParser(prog="ops_pack")
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "eval"):
        s = sp.add_parser(name)
        s.add_argument("--backend", action="append", default=[], help="fake | opencode:<model> | ollama:<model> | codex[:model] | grok-build")
        s.add_argument("--db", default=":memory:")
        s.add_argument("--at", default=None, help="evaluation time for SLA/DQ checks (ISO)")
        s.add_argument("--out")
    s = sp.add_parser("approve"); s.add_argument("--db", required=True); s.add_argument("--id", required=True)
    s.add_argument("--approver", required=True); s.add_argument("--reject", action="store_true")
    s = sp.add_parser("dq"); s.add_argument("--db", required=True); s.add_argument("--at")
    a = p.parse_args(argv)
    {"demo": cmd_demo, "eval": cmd_eval, "approve": cmd_approve, "dq": cmd_dq}[a.cmd](a)


if __name__ == "__main__":
    main()
