# Operation research dependency inventory (WG-14)

## Scope and enforcement

This is the first separation step for an operations-only repository. It does
not remove `src/bithumb_bot/research/`, change approved-profile validation, or
change any runtime execution behavior. The machine-readable temporary
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
| approved profile/promotion/evidence | 11 | Promotion custody and evidence validation must retain their fail-closed checks while moved. |
| CLI command | 4 | Research and data-plane commands are still registered by the monolithic CLI. |
| generic utility | 7 | Hashing and manifest/date-range helpers need operations-owned replacements. |
| test/document/script | 3 | Research diagnostics and test-support helpers should leave with their workflow. |

Counts describe files, not individual imported modules. See the JSON allowlist
for the complete file-by-file inventory and exact import modules.

## Next PR: first migration targets

Move these lowest-risk, clearly bounded modules first, in this order:

1. Create an operations-owned hash utility, then move callers currently using
   only `research.hashing`: `db_snapshot_manifest.py`, `decision_equivalence.py`,
   `h74_equivalence_manifest.py`, `h74_observation_report.py`,
   `h74_restore_check.py`, and `operator_smoke_authority.py`. Preserve content
   hash algorithms and evidence payloads exactly.
2. Move research-only CLI registration and scripts together:
   `cli/commands/research.py`, `cli/commands/data_plane.py`,
   `operator_commands.py`, `notification_diagnostics.py`, and the two
   channel-breakout scripts. Do not leave a command registry path that imports
   research on normal operational startup.
3. Extract the minimal runtime strategy registry/spec/capability interfaces
   required by `runtime_strategy_set.py`, `runtime_strategy_decision.py`,
   `runtime_data_provider.py`, `runtime_adapter_bootstrap.py`, and `config.py`.
   This is a contract extraction, not a shared package/submodule creation.
4. Move approved-profile/promotion/evidence custody only after the above
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
