# Strategy Plugin Authoring

Strategy authoring has three public levels:

1. Level 1: research-only strategies for experiments and backtests
2. Level 2: replay-compatible strategies that can prove deterministic read-only replay but are not live eligible
3. Level 3: live-eligible strategies with runtime adapters, approved-profile binding, and explicit execution capability gates

Live safety is not weakened by the research-only API. A strategy without a promotion extension fails closed for promotion export, runtime replay, live dry-run, and live real-order.

Do not register promotion-grade runtime strategies in `bithumb_bot.strategy.registry`. That module is compatibility-only for smoke strategy policies and legacy DB-bound construction.

## Which Level

Use this decision tree:

- Choose Level 1 when the strategy is exploratory and only needs research/backtest events.
- Choose Level 2 when the strategy needs deterministic replay exports or replay comparison, but must not run in live dry-run or live real-order mode.
- Choose Level 3 only when the strategy needs runtime decisions, live dry-run eligibility, and possibly live real-order eligibility after approved-profile and decision-equivalence gates.

New strategy PRs should normally be small. Level 1 usually needs one plugin file and one focused test file. Level 2 usually adds only the replay-compatible plugin file plus one focused replay contract test. Level 3 adds the explicit runtime adapter/policy assembly surface and focused live/promotion contract tests.

## Level 1: Fast Research Path

Use `bithumb_bot.strategy_authoring.ResearchOnlyStrategyPlugin` or one of its helpers:

- `research_plugin_from_decide_snapshot()`
- `research_plugin_from_event_builder()`

A research-only strategy should provide only:

- `strategy_name`
- `version`
- a plugin-local `StrategySpec`
- `required_data` and optional data
- either a snapshot decision function or a research event builder
- a diagnostics namespace, or the default strategy name

Research-only authors must not declare runtime/live vocabulary such as `StrategyRuntimeCapabilities`, runtime replay builders, runtime parameter adapters, approved-profile requirements, live dry-run eligibility, or live real-order eligibility. The authoring adapter normalizes research-only plugins into the internal registry with `promotion_extension_missing`.

Research-only plugins can run through the generic research/backtest pipeline and emit reproducibility evidence, including strategy spec hash, dataset hash, deterministic decision hashes, `promotion_grade=false`, `promotion_extension_missing_reason=promotion_extension_missing`, and `recommended_next_action=promote_strategy_contract`.

Research-only plugins are not promotion evidence. If promotion is attempted before adding a promotion extension, gates must fail closed with stable reason codes such as:

- `promotion_extension_missing`
- `promotion_runtime_unsupported_for_strategy`
- `runtime_replay_unsupported_for_strategy`
- `live_dry_run_not_allowed_for_strategy`
- `live_real_order_not_allowed_for_strategy`

`threshold_research_only` is the minimal built-in template for this path. It demonstrates that a strategy can be added without runtime replay, live, approved-profile, or adapter boilerplate. `canary_non_sma` is not the minimal template; it remains a promotion-grade architecture proof.

## Level 2: Replay-Compatible Path

Use `bithumb_bot.strategy_authoring.ReplayCompatibleStrategyPlugin`,
`ReplayCompatibleStrategyExtension`, or
`build_replay_compatible_strategy_plugin()`.

A replay-compatible strategy should provide only:

- research identity and a plugin-local `StrategySpec`
- parameter schema and materialization sufficient for deterministic behavior
- pure policy or policy assembly material
- deterministic decision artifact material
- replay fingerprint material
- a read-only replay strategy or replay decision material

Level 2 must not require Settings fields, approved-profile runtime authority,
live dry-run capability, live real-order capability, production runtime
parameter adapters, or execution intent for real orders. Default runtime
capability is fail-closed for live:

- `approved_profile_required=false`
- `live_dry_run_allowed=false`
- `live_real_order_allowed=false`
- `runtime_decision_supported=false`

`replay_threshold` is the minimal built-in template for this path. It shows pure
threshold policy material, parameter schema validation, centralized replay
fingerprints, and read-only SQLite replay without live eligibility.

## Level 3: Promotion-Grade Path

Use `bithumb_bot.strategy_authoring.PromotionGradeStrategyExtension` with
`build_live_eligible_strategy_plugin()`. The builder normalizes the public
authoring object into the registry representation, so new live-eligible strategy
modules should not hand-write `ResearchStrategyPlugin(...)`.

The extension owns the heavy requirements:

- runtime replay builder
- runtime parameter adapter
- runtime decision adapter factory
- policy assembly factory
- export normalizer or equivalence exporter when needed
- approved-profile requirement
- runtime capability declaration
- live dry-run eligibility
- live real-order eligibility
- fail-closed reason

Promotion-grade strategies are normalized into `ResearchStrategyPlugin` for the existing registry, contract hashing, runtime replay, approved profile verification, and live preflight gates. Runtime capability is explicit and must not be inferred from adapter presence.

Runtime fail-safe strategies such as `safe_hold` are outside the research parity target. They may declare typed runtime decision support and policy assembly for fail-closed runtime fallback behavior, but they must remain `research_runnable=false`, have no `research_event_builder`, reject research execution explicitly, and remain ineligible for live real orders unless a separate reviewed promotion contract changes that.

Promotion-bound strategies must preserve existing evidence:

- plugin contract hash
- runtime decision request hash
- replay fingerprint hash
- approved profile hash
- runtime contract hash
- policy hash fields

Production-bound manifests still fail closed when runtime-bound behavior parameters, replay support, runtime adapters, policy assembly, approved-profile evidence, or decision equivalence evidence are missing.

Runtime parameter authority is centralized at the runtime strategy boundary. A
promotion-grade strategy must accept parameters from an approved profile or from
`RuntimeStrategySpec.parameters`; `runtime_parameter_adapter.from_settings()` is
paper legacy compatibility only and must not be required for strict runtime operation.
New strategies should not add strategy-specific fields to `Settings`.
`STRATEGY_PARAMETERS_JSON` is the same paper legacy compatibility surface; it is
not production authority for promotion, live dry-run, or live real-order runtime.

Structured runtime selection uses `RUNTIME_STRATEGY_SET_JSON` with
`market_scope.mode="single_pair"` for the current runtime. Every active strategy
instance must match the configured pair and interval. Multi-pair runtime remains
unsupported until readiness, target state, allocation, execution submit, and
persistence are pair-scoped.

Use `max_target_exposure_krw` for allocator exposure caps. Historical
`risk_budget_krw` inputs are compatibility aliases for that exposure cap and are
not maximum-loss budgets.

At run start, the runtime persists a materialized strategy-set manifest in the
trade DB. It records active instance ids, raw and materialized parameters,
parameter source/audit, approved-profile bindings, plugin/runtime/strategy
hashes, execution and risk config hashes, market scope, exposure-cap semantics,
and deterministic run-start request hashes. Decision bundles, allocation
decisions, and execution plans reference the same manifest hash.

## Required Architecture

The supported research architecture is:

`StrategySpec` -> plugin authoring object -> normalized `ResearchStrategyPlugin` -> plugin-owned `research_event_builder` -> `research.backtest_runner.run_plugin_backtest` -> strategy-neutral `research.backtest_kernel` -> runtime replay, promotion, and live capability gates.

`ResearchStrategyPlugin` is the internal normalized registry representation. It
exists for discovery, contract hashing, capability validation, approved-profile
verification, runtime replay, and live preflight gates. It is not the normal
public authoring API for new strategy modules. Public authoring should use:

- `ResearchOnlyStrategyPlugin` or its helpers for Level 1
- `ReplayCompatibleStrategyPlugin` or `build_replay_compatible_strategy_plugin()` for Level 2
- `PromotionGradeStrategyExtension` with `build_live_eligible_strategy_plugin()` for Level 3

`research.backtest_runner` is generic and strategy-neutral. It may call explicit plugin hooks such as `research_parameter_materializer` and `research_event_builder`, but it must not branch on strategy names or own strategy-specific defaults. Strategy-specific research materialization, exploratory legacy behavior, empty-event policy, event generation, diagnostics, and payload adaptation belong in plugin-owned modules.

`research.strategy_registry` owns normalized contract dataclasses, validation, registration, discovery, listing, resolving, and test reload behavior only. Built-in plugins are loaded through `bithumb_bot.strategy_plugins.iter_builtin_strategy_plugins()` using lazy imports.

Existing `sma_with_filter`, `safe_hold`, and baseline direct
`ResearchStrategyPlugin(...)` construction is allowlisted as legacy migration
surface. New strategy plugin files are guarded by tests and should not directly
construct the internal dataclass.

## StrategySpec Ownership

New strategies should define `StrategySpec` in the plugin module that owns the strategy. This keeps new strategy PRs from modifying common research/runtime engine files.

`research/strategy_spec.py` still contains common dataclasses, validation helpers, compatibility helpers, and historical built-in specs. It is no longer the required central edit point for every new strategy. Existing centralized specs remain for backward compatibility unless a focused migration safely moves them into plugin-local modules.

Architecture guard tests should continue to prevent new strategy-specific branches from entering common research files such as `backtest_runner`, `backtest_kernel`, `backtest_engine`, `backtest_support`, and `strategy_registry`.

## Tests

Level 1 research-only strategy tests should prove:

- registration and discovery
- research/backtest execution through the generic runner
- deterministic reproducibility fields and `promotion_grade=false`
- fail-closed promotion/runtime/live behavior
- no runtime/live/promotion boilerplate in the public authoring path

Level 2 replay-compatible strategy tests should prove:

- deterministic pure policy or deterministic replay decision material
- deterministic replay fingerprint and replay fingerprint hash
- read-only replay behavior
- parameter schema and runtime-bound parameter validation
- `live_dry_run_allowed=false`
- `live_real_order_allowed=false`
- runtime/live preflight fail-closed reason codes

Level 3 live-eligible strategy tests should prove:

- explicit runtime parameter adapter
- runtime decision adapter factory
- replay support when required
- policy assembly
- approved-profile binding
- live dry-run and live real-order capability behavior
- preserved decision, replay, runtime, policy, and profile hashes

New strategy PRs should normally modify one plugin file and one focused test file. They should not add strategy-specific branches to common research or runtime gateway files.

## Compatibility

`ResearchStrategyPlugin` remains the normalized internal registry representation.
Existing code may still inspect it for contract hashes, runtime capability
validation, profile verification, and live preflight. Public strategy authoring
should use `strategy_authoring` instead of hand-writing a broad
`ResearchStrategyPlugin`.

`strategy.registry` is legacy/smoke compatibility only. `research.backtest_engine`, `research.backtest_loop`, and compatibility re-exports from `research.backtest_kernel` are compatibility-only for old import paths and must not regain strategy, risk, execution, or ledger authority.
