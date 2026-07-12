# Retired experiment schema migration

This is an operator-only, offline migration. It is not invoked by startup or
normal reconciliation. The supplied backup path is a recovery-critical SQLite
snapshot and must be an absolute path in the relevant `BACKUP_ROOT/<mode>/db/`
bucket, outside the repository.

legacy retired-schema migration only; not an active runtime feature

## Explicit schema contract

The migration removes only explicitly classified retired `orders` columns. The
known retired H74 columns are:

```text
h74_entry_plan_client_order_id
h74_position_ownership_contract_hash
h74_position_ownership_contract
```

Any `orders` column that is absent from the canonical schema but not on this
retired list fails closed as
`unexpected_noncanonical_orders_columns:<sorted-columns>`. The migration also
fails closed for a non-canonical user-created `orders` index or trigger as
`unexpected_orders_schema_objects:<sorted-names>`. It never silently drops
unknown columns or schema objects. Investigate the source schema and write a
separate approved change specification before attempting the migration again.

The table allowlist is exactly `h74_cycle_state` and
`daily_participation_claims`. An `h74_` prefix is never deletion authority.
An unknown prefixed table stops apply before a backup, temporary `orders`
table, or database mutation with `unexpected_h74_prefixed_tables:<names>`.
Known names are also checked against their historical column contracts; a
mismatch stops with `retired_table_schema_mismatch:<table>:...`.

When `orders` must be rebuilt, the migration restores every canonical explicit
schema object (indexes, partial/unique indexes, and triggers), then compares
both the explicit-object and SQLite auto-index inventories before commit. The
mode-specific run lock is held from the locked DB reconnect through source
checks, backup, rebuild, drop, post-check, and commit. A lock conflict is
`migration_run_lock_unavailable`.

## Required operator sequence

1. Create a copy of the operational DB; work on the copy first.
2. Run the retired runtime-state cleanup dry-run.
3. Review the reported shared virtual and pair target state.
4. If warranted, apply runtime-state cleanup and retain its backup hash.
5. Run this schema migration dry-run.
6. Apply this schema migration.
7. Verify integrity and a backup restore.
8. Deploy the new code and perform live dry-run.
9. Reconcile, then restart.
10. Only then re-arm real orders.

The runtime-state tool does not infer strategy ownership for
`target_position_state`. It may clear that pair state only with
`--clear-pair-target-state`, a flat portfolio, zero executable open lots, zero
risky orders, broker/local convergence attestation, a verified backup, and the
explicit confirmation. It removes only matching retired virtual rows for the
specified pair. Orders, fills, trades, lifecycle rows, order events, broker fill
observations, and execution-quality audit rows are protected and checked before
and after mutation.

```bash
uv run python tools/migrations/remove_retired_strategy_runtime_state.py \
  --mode live --pair KRW-BTC \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-runtime-state.20260712T103000Z.sqlite

uv run python tools/migrations/remove_retired_strategy_runtime_state.py \
  --mode live --pair KRW-BTC \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-runtime-state.20260712T103000Z.sqlite \
  --broker-local-converged --clear-pair-target-state --apply \
  --confirm REMOVE_RETIRED_STRATEGY_RUNTIME_STATE
```

Historical orders, fills, trades, and audit evidence are preserved. Unknown
H74-prefixed tables are investigation targets, never automatically removed.

## Environment preparation

Before a live migration, explicitly load the production environment containing
at least:

```text
MODE
ENV_ROOT
RUN_ROOT
DATA_ROOT
LOG_ROOT
BACKUP_ROOT
ARCHIVE_ROOT
DB_PATH (when used)
```

Live managed roots must be absolute, repository-external, mode-neutral, and
must not overlap or have parent/child relationships with one another.

1. Confirm the deployed revision and stop the bot service. Do not run this
   migration while the service is running or while real orders are armed.
2. Verify a staging copy first. Do not combine code deployment and the DB
   migration in the same change moment.
3. Confirm no process holds the database, no unresolved/open order exists, and
   broker and local positions are converged. Do not attest convergence until an
   operator has actually checked it.
4. Choose a new, unique backup filename in `BACKUP_ROOT/<mode>/db/`. Existing
   backup paths are refused and are never overwritten.
5. Run the default dry-run:

```bash
uv run python tools/migrations/remove_retired_experiment_schema.py \
  --mode live \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-schema.20260712T103000Z.sqlite
```

6. Review the reported tables and columns, then apply with the explicit
   operator convergence attestation:

```bash
uv run python tools/migrations/remove_retired_experiment_schema.py \
  --mode live \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-schema.20260712T103000Z.sqlite \
  --broker-local-converged \
  --apply \
  --confirm REMOVE_RETIRED_EXPERIMENT_SCHEMA
```

The command validates that both paths are absolute, repository-external, and
mode-separated. The DB must be the PathManager canonical DB location for the
selected mode, or its configured `DB_PATH` override within that mode's managed
`trades/` bucket; the backup must be in that mode's managed `db/` backup bucket.
It validates source SQLite integrity and foreign keys before backup, validates
the backup after creation (including retained `orders` data), and only then
begins its one transaction. The transaction verifies all retained canonical
`orders` columns, row counts, identifiers, status/side distributions, and
SQLite integrity before commit.

On success the report has `status=applied`, `backup_created=true`,
`database_modified=true`, and `backup_sha256`. Preserve that hash in the
operator record. Do not delete the backup until the retention policy and a
restore verification allow it.

Re-running against a DB with no retired schema reports `status=already_clean`.
That is a true no-op: it does not create, open, overwrite, or otherwise modify
the supplied backup path, and it does not modify the DB. A migration that is
still needed but receives an existing backup filename is refused with
`backup_path_already_exists`; choose a new unique name.
