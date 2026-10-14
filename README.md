# ops-pack-demo

Rules-first routing of internal **Finance / Legal / BizOps** requests, with SQL enrichment, de-duplication,
a human approval gate and nightly data-quality checks. A language model is used for exactly one thing:
classifying free text that no rule matched, and its answer must pass a JSON schema before it counts.

Python 3.10+ standard library only (`sqlite3`). No keys, no network unless you choose a model backend.

```
request events ──► ingest + dedupe ──► SQL enrich ──► rules ──► (no rule?) model classify ─► schema check
                                                         │                                    │ fail / low confidence
                                                         ▼                                    ▼
                                         approval gate (money, contracts)               triage queue
                                                         ▼
                                              nightly SQL data-quality checks
```

## What it does
| step | how | why |
|---|---|---|
| ingest + dedupe | exact (`source`+`external_id`) and near duplicates (requester, subject, amount, currency, vendor) | the same invoice arrives by chat and email |
| enrich | SQL joins: requester → team, vendor by normalised name (company suffixes dropped) or by email domain | routing and checks run on trusted data |
| route | ordered keyword rules (data, `DEFAULT_RULES`) → team, owner, SLA | cheap, predictable, auditable |
| classify | only unmatched free text; pluggable backend; output parsed, validated against `CLASSIFY_SCHEMA`, needs `min_confidence` | the model never sees routed requests and never approves |
| approval gate | amount > 0 → finance approver; contract words → legal/finance approver; never the requester; unknown people rejected | money and contracts stay human decisions |
| data quality | 7 plain-SQL checks: SLA breached, duplicate vendors, missing org number, missing owner, stale vendor record, unknown requester, money without vendor; findings stored idempotently | keeps the data trustworthy over time |
| audit | every ingest/route/decision written to `audit` | who decided what, and on which rule |

## Run
```bash
python -m ops_pack demo --backend fake --at 2026-10-15T09:00:00     # full pipeline on the fictional fixtures
python -m ops_pack eval --backend ollama:llama3.2:3b --out results.json
python -m ops_pack demo --db ops.db --backend fake && python -m ops_pack approve --db ops.db --id chat:c1 --approver sam@company.example
python -m ops_pack dq --db ops.db                                     # schedule nightly (cron / CI)
python -m unittest discover -s tests -t .                            # 14 tests, no network
```

### Model backends (`--backend`)
| spec | uses | configure |
|---|---|---|
| `fake` | deterministic heuristic for tests | – |
| `opencode:<model>` | OpenCode CLI (`opencode run -m opencode/<model>`), e.g. free models | `OPS_OPENCODE_CMD` wraps the call, e.g. a sandbox script; `{workdir}` is replaced by a temp dir |
| `ollama:<model>` | local Ollama `/api/chat` with JSON mode | `OLLAMA_HOST` (default `127.0.0.1:11434`) |
| `codex[:<model>]` | Codex CLI `codex exec`, low reasoning effort | the CLI's own login |
| `grok-build` | Grok Build headless `grok -p`, web search off | the CLI's own login |

Each backend is a plain `prompt -> text` callable, so adding another provider is a few lines in `ops_pack/backends.py`.
Request text is wrapped in markers and labelled untrusted; whatever comes back must still pass the schema.

## Results: free-text classification (2026-10-14)
12 hand-labelled requests that **no rule matches** (`fixtures/eval_free_text.json`), one call each, temperature default.
Accuracy = valid JSON *and* the right team. Raw rows: `results/eval-2026-10-14.json`.

| backend | model | accuracy | schema-valid | median latency |
|---|---|---|---|---|
| OpenCode (free) | big-pickle | 100% | 100% | 5.9 s |
| OpenCode (free) | space-bunny-free | 100% | 100% | 5.1 s |
| OpenCode (free) | cat-jam-free | 100% | 100% | 5.0 s |
| OpenCode (free) | mimo-v2.6-flash-free | 100% | 100% | 9.6 s |
| Codex CLI | default model, low effort | 100% | 100% | 4.0 s |
| Grok Build | grok-4.7-build | 100% | 100% | 6.4 s |
| Ollama (local, CPU) | llama3.2:3b | 83% | 92% | 2.7 s |
| Ollama (local, CPU) | gemma2:2b | 58% | 58% | 1.8 s |
| Ollama (local, CPU) | qwen2.5:1.5b | 25% | 33% | 1.4 s |

Reading it: the hosted models saturate this small set, so it separates "good enough" from "not yet", not the hosted models
from each other. Small local models often break the JSON contract. The schema check catches that, and those requests go to the
triage queue instead of a wrong team. That fallback is the point of the design: a weak model can only cost a human some
triage time, it cannot misroute money or approve anything. A larger, harder set is the next step (see below).

## Layout
`ops_pack/core.py` pipeline + SQL · `ops_pack/backends.py` model adapters · `ops_pack/__main__.py` CLI ·
`fixtures/` fictional reference data, requests, eval set · `tests/` · `RUNBOOK.md` owner, schedule, access rules, failure modes.

## Next
- Harder eval set (ambiguous and multi-team requests, Swedish text) and confidence calibration per model.
- Postgres variant of the schema; webhook intake for chat/tracker/ATS events; MCP tool wrapper with scoped permissions.

All names, people and companies in `fixtures/` are fictional. MIT licence.
