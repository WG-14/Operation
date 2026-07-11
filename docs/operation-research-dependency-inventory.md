# Operation research dependency inventory (WG-14)

## Runtime cutover complete

Operation runtime strategy discovery, specifications, capabilities, parameter
authority, data requirements, decision adapters, replay builders, exit-policy
materialization, and paper stress execution are Operation-owned. Runtime does
not import the research package.

`OperationStrategyPlugin` is intentionally runtime-only. It carries the
strategy identity/specification, capabilities, runtime parameter adapter,
decision adapter factory, feature/data/replay builders, policy assembly,
exit-policy materializer, and decision-evidence contract. It excludes research
runners, datasets, decision events, manifests, and exports.

Built-ins are registered by `bithumb_bot.operation_strategy.discovery` from
`bithumb_bot.operation_strategy.builtin`; optional extensions use the
`bithumb_bot.operation_strategy_plugins` entry-point group. The runtime
bootstrap, config validation, strategy-set materialization, inventory, and
runtime data provider resolve this registry directly.

## Enforced residual allowlist

`tests/policy/operation_research_import_allowlist.json` is an exact AST
allowlist. It contains exactly six files, all in
`approved profile/promotion/evidence` custody:

- `approved_profile.py`
- `evidence_safety.py`
- `execution_quality.py`
- `h74_observation.py`
- `paired_experiment.py`
- `profile_cli.py`

No runtime strategy file is allowed in this list. The boundary test rejects
any additional non-research source import.

## Removed research-only Operation sources

Channel breakout, threshold-only, baseline plugins/events, research event
builders, shared strategy authoring/backtest helpers, and the research contract
test helper were removed from Operation. Live submit, recovery, run-lock, and
fill-deduplication paths were not changed.
