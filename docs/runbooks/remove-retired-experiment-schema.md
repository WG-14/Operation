# Removed strategy retirement

`retire_removed_strategy.py` is a temporary, offline operator migration. It
removes the retired H74 / `daily_participation_sma` schema and virtual runtime
state in one reviewed plan, one SQLite backup, and one transaction. It is not a
startup migration, reconcile action, or normal bot command.

The plan and backup are recovery-critical runtime artifacts. Store plans under
`DATA_ROOT/<mode>/reports/` and backups under `BACKUP_ROOT/<mode>/db/`; neither
may be inside the repository. Paper and live plans, locks, databases, and
backups remain completely separate.

## Retired-state contract

The tool removes only the following `orders` columns:

```text
h74_entry_plan_client_order_id
h74_position_ownership_contract_hash
h74_position_ownership_contract
daily_participation_policy_hash
daily_count_snapshot_hash
participation_decision_hash
daily_participation_kst_day
daily_participation_fallback_mode
```

It removes only `h74_cycle_state` and `daily_participation_claims`, after their
historical schemas are validated. Unknown noncanonical `orders` columns,
indexes, triggers, and unknown `h74_` tables stop the migration. A prefix is
never deletion authority.

For the requested pair, it removes only virtual target rows satisfying:

```sql
strategy_name = 'daily_participation_sma'
OR strategy_instance_id LIKE 'daily_participation_sma:%'
OR strategy_instance_id LIKE 'h74%'
```

Other pairs and strategies are preserved. The protected ledger tables are
`orders`, `fills`, `trades`, `trade_lifecycles`, `order_events`,
`broker_fill_observations`, and `execution_quality_events`. Their typed,
canonical row inventories are checked before backup and before commit.

## Operator procedure

1. Stop the service and load the production environment. Do not run against a
   live operational DB before first validating a copy.
2. Confirm no process holds the DB, no unresolved/risky order exists, and the
   broker and local state are converged.
3. Choose a unique backup path and create a plan. If a pair target state exists,
   choose `retain` or `clear`; omission is intentionally refused as
   `operator_decision_required`.
4. Review the JSON plan, its `status`, `blockers`, actions, and `plan_hash`.
   A `clear` plan additionally requires a flat portfolio and zero executable
   open lots for the pair.
5. Preserve the reviewed plan JSON and hash, then apply that exact plan.
6. Preserve the result and backup SHA-256. Verify the backup, run DB integrity
   checks, then perform live dry-run, a HOLD cycle, reconciliation, restart,
   and only then re-arm real orders.

```bash
uv run python tools/migrations/retire_removed_strategy.py plan \
  --mode live --pair KRW-BTC \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retirement.20260712T120000Z.sqlite \
  --target-state-action clear \
  --output /var/lib/bithumb-bot/data/live/reports/retirement-plan.json

uv run python tools/migrations/retire_removed_strategy.py apply \
  --plan /var/lib/bithumb-bot/data/live/reports/retirement-plan.json \
  --plan-hash sha256:<reviewed-hash> \
  --broker-local-converged \
  --confirm RETIRE_REMOVED_STRATEGY

uv run python tools/migrations/retire_removed_strategy.py verify-backup \
  --plan /var/lib/bithumb-bot/data/live/reports/retirement-plan.json \
  --backup /var/backups/bithumb-bot/live/db/live.before-retirement.20260712T120000Z.sqlite
```

`plan` is read-only: it does not create a backup or modify the DB. `apply`
uses the same planning engine while holding the mode-specific run lock. It
refuses stale source fingerprints, schema/ledger/runtime-state changes, target
state changes, and a newly occupied backup path with
`retirement_plan_stale`. No row content is reported in stale-plan output.

`retain` never modifies the pair target row. A successful retirement with a
retained target row returns `applied_with_retained_target_state`; it is never
described as `already_clean`. `clear` requires the reviewed plan hash, explicit
confirmation, broker/local convergence attestation, a verified backup, an exact
pair match, a flat portfolio, zero open executable lots, and no risky orders.

All command stdout is canonical JSON. Safety refusals return exit code 2;
successful plan, apply, and backup verification return exit code 0. The plan
report is a diagnostic artifact in `data/<mode>/reports/`; the backup is a
recovery-critical timestamped SQLite snapshot in `backup/<mode>/db/` and is
never overwritten.

## Sunset contract

This migration must be deleted in a separate final cleanup once all of the
following are true:

1. Paper and live database retirement are complete.
2. Each environment's backup hash is recorded and its restore is verified.
3. No retired columns, tables, or virtual state remain.
4. Runtime code no longer needs legacy DB compatibility.
5. The required retention period and rollback window have ended.

That cleanup deletes the CLI, shared engine, this test suite, and this runbook:
`tools/migrations/retire_removed_strategy.py`,
`tools/migrations/_offline_retirement.py`,
`tests/test_removed_strategy_retirement.py`, and this document.
