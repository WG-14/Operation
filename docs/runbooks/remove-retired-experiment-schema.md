# Retired experiment schema migration

This is an operator-only, offline migration. It is not invoked by startup or
normal reconciliation. The supplied backup path is a recovery-critical SQLite
snapshot and must be an absolute path in the relevant `BACKUP_ROOT/<mode>/db/`
bucket, outside the repository.

legacy retired-schema migration only; not an active runtime feature

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
