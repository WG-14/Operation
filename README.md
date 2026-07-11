# bithumb-bot

Safety-first Bithumb trading bot. Preventing wrong orders, duplicate orders,
state corruption, and unsafe recovery takes precedence over profitability.

Runtime artifacts belong outside the repository and are resolved only through
`PathManager`. Paper and live storage remain fully separated; live requires
explicit, repository-external absolute runtime roots.

## Operation approval

Live dry-run and armed live operation require an external `OPERATION_APPROVAL_PATH`.
An approval is fail-closed against the active Operation strategy name/version,
strategy spec and plugin contract hashes, materialized parameters, exit policy,
market/interval, risk policy, execution contract, allowed mode, expiry, maximum
order amount, and content hash. It does not consume external provenance inputs.

```bash
uv run bithumb-bot operation-approval-create \
  --out /var/lib/bithumb-bot/approvals/btc.json \
  --approved-by operator \
  --expires-at 2026-12-31T00:00:00+00:00 \
  --allowed-mode live_dry_run
uv run bithumb-bot operation-approval-verify --approval /var/lib/bithumb-bot/approvals/btc.json
```

The canonical CLI is `uv run bithumb-bot <command>`. Useful commands include
`health`, `status`, `sync`, `run`, `live-dry-run`, `reconcile`, `resume`,
`ops-report`, and `execution-quality-report`.

## Validation

Codex patch sessions run only focused tests. The full suite remains owned by
the dedicated CI/WSL pipeline. See [storage-layout.md](docs/storage-layout.md)
and [runtime-data-policy.md](docs/runtime-data-policy.md) for the runtime
storage contract.
