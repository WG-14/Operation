# operation

Safety-first investment strategy operation and execution runtime.

Operation is not connected to any exchange. Paper and research execution are
offline-first: runtime candles are read from the mode-separated SQLite store
and are never implicitly synchronized from the network. If no candle data is
available, the cycle returns its normal no-data result.

Live execution is deliberately fail-closed. No broker adapter is configured,
so a live startup is blocked with `LIVE_BROKER_NOT_CONFIGURED` before
reconciliation or submission can begin. Live requests are never simulated as
paper orders.

Future integrations belong behind `operation.broker.base.Broker` and the
`BrokerFactory` boundary in `operation.broker.availability`. New market-data
integrations must implement the narrow provider boundary in
`operation.marketdata_provider`; they must be explicitly selected and must
not become a paper default.

Runtime artifacts are resolved by `PathManager` outside the repository. New
runtime defaults use `~/.local/state/operation`; existing data under older
runtime locations is intentionally neither moved nor deleted.

```bash
uv run operation --help
uv run operation health
uv run operation config-dump --masked
```
