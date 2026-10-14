# Runbook: ops pack

**Owner:** the ops automation owner (one named person) · **Backup:** a named person in BizOps · **Review cadence:** monthly, plus after any rule change.

## Schedule
| job | when | command |
|---|---|---|
| intake + route | on each new event (or every 5 min) | `python -m ops_pack demo --db ops.db --backend <spec>` (replace the fixture loader with your intake) |
| data-quality | nightly 02:00 | `python -m ops_pack dq --db ops.db` → review new rows in `dq_findings` |
| eval | weekly / before changing model | `python -m ops_pack eval --backend <spec> --out results.json`; switch models only if accuracy and schema-valid do not drop |

## Access rules
- The model backend receives only the subject and body of requests that **no rule matched**. Amounts, vendors and people data stay in SQL.
- Only people in the `people` table can approve; money needs a finance approver, contracts need legal (or finance). Requesters cannot approve their own requests. The model has no approval path.
- `ops.db` holds business data: keep it on the ops host, back it up nightly, and grant read access to Finance/Legal/BizOps leads only.
- Model CLIs use their own logins; no keys are stored by this tool.

## Failure modes
| symptom | check | action |
|---|---|---|
| many requests in `ops-triage` | `SELECT reason, count(*) FROM requests WHERE owner='ops-triage' GROUP BY 1` | schema failures → model/backend problem (run eval); "no rule" → add a rule |
| model backend down | `reason` contains "backend error" | routing continues; unmatched requests go to triage. Switch `--backend` |
| SLA breaches pile up | `dq_findings` where `check_name='sla_breached'` | reassign owner or raise SLA in `DEFAULT_RULES` (with the team lead) |
| duplicate vendors | `duplicate_vendors` finding | merge in the vendor master, keep the record with an org number |

## Changing rules
Rules are data (`DEFAULT_RULES`). Add a rule, add a fixture and a test, run the tests, then deploy. Record the change and the owner's OK.
