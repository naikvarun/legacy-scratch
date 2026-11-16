# AI-Assisted Legacy Data Migration Platform

> **Status: scaffold.** The graph, state, review gate, checkpointer, audit log and LangFuse
> wiring are real and run end to end. The parts that touch outside systems are stubs:
>
> - the LLM (`LLM_MODE=stub` uses canned responses)
> - source profiling
> - the warehouse load
> - Great Expectations and dbt
>
> Each stub returns output in the shape the real implementation will produce.

## Project Overview
LangChain agents infer column meanings and write transformation rules. LangGraph runs
the pipeline, and a confidence gate stops it for a human decision on every high-risk
mapping and rule. Every AI suggestion and human decision is written to a hash-chained
audit log and traced in LangFuse.

## Architecture Description
```
schema_profiler → ai_mapper → human_review_gate → rule_generator ─┬→ migration_executor → validator → doc_generator
                                   ▲  (mapping stage)              │
                                   └──── rules < threshold ────────┘  (rule stage)
```

| Piece | Where |
|---|---|
| State and models (rule schema from Section 07) | `workflow/state.py` |
| Graph, routing and CLI | `workflow/langgraph_orchestrator.py` |
| The 7 nodes | `workflow/nodes.py` |
| Confidence gate (`interrupt()`) | `workflow/gates.py` |
| Agents | `agents/` |
| Versioned prompts | `prompts/` |
| LLM-call plumbing (prompt_id, tracing, audit) | `agents/llm.py` |
| Confidence calibration caps | `agents/ai_mapper.py` |
| Approval guard, FK load order, SQL generation, retries | `executor/migration_executor.py` |
| Audit log | `audit/logger.py` |
| LangFuse wiring | `observability/tracing.py` |
| Review interface | `review/cli.py` |
| Synthetic source database | `source_db/` (see its README) |

## Setup Instructions
```bash
uv sync                      # Python 3.10+
cp .env.example .env         # fill in credentials; LangFuse is skipped if its keys are empty
(cd source_db && docker compose up -d --wait && uv run seed.py)   # optional until profiling is real
```

## Environment Variables
All variables are listed in `.env.example`:

- `LLM_MODE`, `LLM_API_KEY`
- `SOURCE_DB_URL`
- `SNOWFLAKE_*`, `BIGQUERY_*`
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
- `CONFIDENCE_THRESHOLD` (default 0.80)
- `MIGRATION_WORKDIR`

## How to Run the Migration Pipeline
```bash
M="uv run python -m workflow.langgraph_orchestrator"
$M run                                   # pauses at the mapping gate (exit code 2)
$M status --run-id <ID>                  # what is pending
$M review --run-id <ID>                  # interactive review, or:
$M review --run-id <ID> --decisions review/sample_decisions.json
$M run --decisions review/sample_decisions.json    # non-interactive, end to end
```
When the pipeline pauses, it writes the review request to `review/queue/<run_id>.json`.
Outputs go to `runs/<run_id>/`:

- `profile.json`
- `mappings.json`
- `rules.json`
- `sql/*.sql`
- `reconciliation_report.json`
- `data_dictionary.md` and `data_dictionary.json`

Exit codes: `0` completed, `2` waiting for review, `1` failed or crashed.

## How to Rollback a Failed Migration
- **Crash or transient failure:** `$M resume --run-id <ID>` continues from the last
  LangGraph checkpoint (`checkpoints/langgraph.sqlite`). Work that already finished is not
  repeated. To try this, run `$M run --decisions … --fail-at migration_executor`.
- **Validation failure:** the run ends `FAILED` before `doc_generator`. Staged tables are
  never swapped into the target, so you only need to drop the `RAW_<run>` and `STG_<run>`
  schemas. Snowflake swap-back and Time Travel steps will be added with the real executor.

## How to Inspect AI Decisions (Audit Log + LangFuse)
- `audit/audit_log.jsonl` is the source of truth. It is append-only, and each entry holds
  the SHA-256 of the previous one. `audit/migration_audit_log.json` is exported after every
  command.
- `$M verify-audit` detects any edited, deleted or reordered entry.
- Each `llm_call` entry records the `prompt_id`, the prompt version, the SHA-256 of the
  rendered prompt and the output.
- Human decisions record the reviewer, the note, the changes and the values before them.
- The `prompt_id` on every rule links to that `llm_call` entry.
- LangFuse holds one trace per run, with trace ID = `create_trace_id(seed=run_id)` and
  session = `run_id`. It contains spans for every node and LLM call, `mapping_confidence`
  scores and `human_*_decision` scores. The same trace ID is stored in every audit entry.

## Known Limitations
- The LLM, profiler, warehouse load, Great Expectations and dbt are stubs (see Status above).
- The `logic` of each rule is not yet checked with sqlglot before approval.
- `DECISIONS.md` has not been written yet.

## Tests
`uv run pytest`. The tests cover:

- both gate stages
- rejection of invalid decisions
- crash and resume
- validation failure blocking completion
- the executor guard
- audit tamper detection
- the calibration caps
