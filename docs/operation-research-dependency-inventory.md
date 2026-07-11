# Operation research dependency inventory (WG-14)

## Scope and enforcement

This is the first separation step for an operations-only repository. It does
not remove `src/bithumb_bot/research/`, change approved-profile validation, or
change any runtime execution behavior. The first operations-owned hashing
migration is complete: operations callers use `bithumb_bot.artifact_hashing`
with fixed research-compatible golden-vector tests. The machine-readable temporary
allowlist is
`tests/policy/operation_research_import_allowlist.json`; it inventories every
direct Python import of `bithumb_bot.research` (or relative `research`) from
non-research package source and repository scripts. The boundary test compares
the AST-discovered imports exactly against that allowlist, so new imports,
removed imports, or changed imported modules require an explicit review.

The inventory categories are:

- `runtime strategy registry/spec/capability`
- `approved profile/promotion/evidence`
- `CLI command`
- `generic utility`
- `test/document/script`

Every entry records its category, imported modules, and reason in the
allowlist. Test files themselves are intentionally outside this source
boundary: they may test both sides during the transition.

## Current inventory summary

| Category | Files | Separation meaning |
| --- | ---: | --- |
| runtime strategy registry/spec/capability | 27 | Shared plugin registry/specification contracts are the primary coupling. |
| approved profile/promotion/evidence | 6 | Promotion custody and evidence validation must retain their fail-closed checks while moved. |
| CLI command | 0 | Operation CLI no longer imports research-owned command helpers. |
| generic utility | 0 | Historical backfill now uses the operations-owned `bithumb_bot.date_range.DateRange`. |
| test/document/script | 1 | Only the research-backed strategy contract test helper remains. |
| **Total** | **34** | **Files with reviewed temporary research-import coupling.** |

Counts describe files, not individual imported modules. See the JSON allowlist
for the complete file-by-file inventory and exact import modules.

Historical backfill date-range separation is complete: it no longer imports the
research manifest for date parsing. `bithumb_bot.date_range.DateRange` preserves
the existing inclusive UTC start/end timestamp and `as_dict()` contract without
a research dependency.

Channel-breakout research diagnostic wrapper removal is complete. The two
research-only script wrappers have left Operation; the only remaining
`test/document/script` entry is `src/bithumb_bot/strategy_contract_testing.py`.

## Next migration targets

Do not reintroduce the completed historical backfill date-range or
channel-breakout diagnostic wrapper dependencies into Operation scripts or
commands. Move these remaining bounded modules in this order:

1. The next large task is to extract the minimal Operation-owned runtime
   strategy registry/spec/capability interfaces
   required by `runtime_strategy_set.py`, `runtime_strategy_decision.py`,
   `runtime_data_provider.py`, `runtime_adapter_bootstrap.py`, and `config.py`.
   This is a contract extraction, not a shared package/submodule creation.
2. Move approved-profile/promotion/evidence custody only after the above
   interfaces are stable. Preserve all lineage, deployment-policy, and
   production-calibration validation as fail-closed gates.

Do not migrate live execution, recovery, run-lock, order submission, or
duplicate-fill handling as part of these first moves.

## Operation-focused test runner

`./scripts/run_operation_tests.sh` runs a curated P0/P1 operational set:
import boundary, runtime authority boundaries, live preflight, mode-scoped run
lock, fill dedupe, submit hardening, execution-service contract, recovery,
restart recovery, and lot-native authority. It sanitizes broker credentials and
notification environment and uses an external pytest workspace. It deliberately
does not run research suites or a selector-less test run.
