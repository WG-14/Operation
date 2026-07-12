# Retired experiment schema migration

This is an operator-only, offline migration. It is not invoked by startup or
normal reconciliation. The supplied backup path is a recovery-critical SQLite
snapshot and must be an absolute path in the relevant `backup/<mode>/db/`
bucket, outside the repository.

legacy retired-schema migration only; not an active runtime feature

1. Confirm the deployed revision and stop the bot service.
2. Confirm no process holds the database, no unresolved/open order exists, and
   broker and local positions are converged.
3. Run the default dry-run:

```bash
uv run python tools/migrations/remove_retired_experiment_schema.py \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-schema.sqlite
```

4. Review the reported tables and columns, then apply with the explicit
   operator convergence attestation:

```bash
uv run python tools/migrations/remove_retired_experiment_schema.py \
  --db /var/lib/bithumb-bot/data/live/trades/live.sqlite \
  --backup /var/backups/bithumb-bot/live/db/live.before-retired-schema.sqlite \
  --broker-local-converged \
  --apply \
  --confirm REMOVE_RETIRED_EXPERIMENT_SCHEMA
```

The command creates the backup before mutation, reports its SHA-256, rebuilds
only the canonical `orders` table while preserving rows, then checks foreign
keys and SQLite integrity. Re-running after success is a no-op.
