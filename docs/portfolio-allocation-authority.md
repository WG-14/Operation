# Portfolio Allocation Authority

Strategy output is not execution authority.

The runtime authority chain is:

```text
StrategyDecisionV2
  -> StrategyPreference
  -> SignalAggregator
  -> PortfolioAllocator
  -> authoritative PortfolioTarget
  -> risk / readiness / target-delta planning
  -> ExecutionSubmitPlan
  -> execution service
```

`StrategyDecisionV2.execution_intent` is a non-authoritative strategy hint. It may be serialized for reproducibility and diagnostics, but it does not decide final portfolio exposure, final order size, conflict resolution, or live submit eligibility.

## Contracts

- `StrategyPreference` records a strategy's typed preference: signal direction, desired exposure or weight hints, confidence, horizon, risk budget, reason, policy hashes, position snapshot hash, and non-authoritative execution intent hint.
- `SignalAggregator` validates typed strategy preferences and creates a deterministic preference set.
- `PortfolioAllocator` converts one or more preferences into one authoritative `PortfolioTarget` per pair.
- `PortfolioTarget` carries allocator policy, allocator config hash, strategy contribution hash, allocation input hash, final target hash, conflict metadata, authoritativeness, and fail-closed reason.
- `ExecutionSubmitPlan` remains the final execution authority.

## Single Strategy

Single-strategy runtime is the degenerate multi-strategy case. The selected strategy still produces `StrategyDecisionV2`, but the run loop adapts it to `StrategyPreference`, aggregates it, allocates a `PortfolioTarget`, and only then invokes execution planning.

For the initial deterministic allocator policy:

- `BUY` targets configured target exposure.
- `SELL` targets zero exposure.
- `HOLD` maintains the previous persisted target exposure when available.
- `HOLD` without previous target exposure fails closed.

## Multi Strategy Conflicts

The initial policy supports deterministic priority allocation. Strategies default to equal priority. If equal-priority top strategies conflict between `BUY` and `SELL`, allocation fails closed instead of guessing.

Conflict metadata is included in the allocation decision, target, logs, and decision context:

- selected priority
- selected strategies
- selected signals
- conflict count
- primary block reason

## Fail Closed

Target-delta execution planning blocks when allocator authority is missing or malformed:

- missing strategy preference
- missing portfolio allocation
- non-authoritative portfolio target
- missing or inconsistent portfolio target hash
- missing allocator input hash
- missing strategy contribution hash
- legacy dict/context-only live real-order path

Observability dictionaries remain non-authoritative. Live real-order submission still requires typed execution summary and typed `ExecutionSubmitPlan`, and target-delta live submission additionally requires authoritative portfolio target metadata on the typed plan.
