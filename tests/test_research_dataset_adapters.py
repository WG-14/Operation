from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from bithumb_bot.research.dataset_snapshot import (
    Candle,
    DatasetQualityReport,
    DatasetSnapshot,
    _build_source_agnostic_dataset_quality_report,
    load_dataset_split,
)
from bithumb_bot.research.datasets.contracts import DatasetLoadContext, UnsupportedDatasetAdapterError
from bithumb_bot.research.datasets.registry import default_dataset_adapter_registry
from bithumb_bot.research.experiment_manifest import DateRange, parse_manifest
from bithumb_bot.research.validation_protocol import ResearchValidationError, _validate_dataset_adapter_provenance


def _manifest(source: str = "sqlite_candles", top_source: str | None = None):
    dataset: dict[str, object] = {
        "source": source,
        "snapshot_id": "adapter_unit",
        "train": {"start": "2023-01-01", "end": "2023-01-01"},
        "validation": {"start": "2023-01-02", "end": "2023-01-02"},
    }
    if top_source is not None:
        dataset["top_of_book"] = {"source": top_source, "missing_policy": "warn"}
    return parse_manifest(
        {
            "experiment_id": "adapter_unit",
            "hypothesis": "Dataset adapters are resolved outside manifest parsing.",
            "strategy_name": "sma_with_filter",
            "market": "KRW-BTC",
            "interval": "1m",
            "dataset": dataset,
            "parameter_space": {"SMA_SHORT": [2], "SMA_LONG": [4]},
            "cost_model": {"fee_rate": 0.0, "slippage_bps": [0]},
            "acceptance_gate": {
                "min_trade_count": 1,
                "max_mdd_pct": 99,
                "min_profit_factor": 0.1,
                "oos_return_must_be_positive": False,
                "parameter_stability_required": False,
            },
        }
    )


class UnitCandleAdapter:
    source = "unit_candles_adapter_source"
    adapter_name = "unit_candle_adapter"
    adapter_version = "1"
    supported_top_of_book_sources = frozenset()

    def load_range(self, *, manifest, split_name: str, date_range: DateRange, context: DatasetLoadContext) -> DatasetSnapshot:
        return DatasetSnapshot(
            snapshot_id=manifest.dataset.snapshot_id,
            source=manifest.dataset.source,
            market=manifest.market,
            interval=manifest.interval,
            split_name=split_name,
            date_range=date_range,
            candles=(
                Candle(date_range.start_ts_ms(), 100.0, 101.0, 99.0, 100.0, 1.0),
            ),
        )

    def quality_report(self, *, snapshot: DatasetSnapshot, context: DatasetLoadContext) -> DatasetQualityReport:
        return _build_source_agnostic_dataset_quality_report(
            db_path=None,
            snapshot=snapshot,
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
            adapter_provenance={"unit": {"source": self.source}},
        )

    def provenance(self, *, manifest, context: DatasetLoadContext) -> dict[str, object]:
        return {
            "dataset_source": manifest.dataset.source,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
        }


def test_sqlite_adapter_registered_by_default() -> None:
    adapter = default_dataset_adapter_registry().resolve("sqlite_candles")

    assert adapter.adapter_name == "sqlite_candle_adapter"
    assert "sqlite_orderbook_top_snapshots" in adapter.supported_top_of_book_sources


def test_manifest_parser_accepts_non_sqlite_source_but_registry_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest("unknown_research_source")

    assert manifest.dataset.source == "unknown_research_source"
    with pytest.raises(UnsupportedDatasetAdapterError, match="unsupported_dataset_adapter:unknown_research_source"):
        load_dataset_split(db_path=tmp_path / "unused.sqlite", manifest=manifest, split_name="train")


def test_registered_non_sqlite_adapter_loads_without_manifest_parser_change(tmp_path: Path) -> None:
    default_dataset_adapter_registry().register(UnitCandleAdapter())
    manifest = _manifest("unit_candles_adapter_source")

    snapshot = load_dataset_split(db_path=tmp_path / "unused.sqlite", manifest=manifest, split_name="train")

    assert snapshot.source == "unit_candles_adapter_source"
    assert [candle.close for candle in snapshot.candles] == [100.0]


def test_unknown_top_of_book_source_fails_at_resolver_not_parser(tmp_path: Path) -> None:
    manifest = _manifest("sqlite_candles", top_source="unknown_top_source")

    assert manifest.dataset.top_of_book is not None
    assert manifest.dataset.top_of_book.source == "unknown_top_source"
    with pytest.raises(UnsupportedDatasetAdapterError, match="unsupported_top_of_book_adapter:unknown_top_source"):
        load_dataset_split(db_path=tmp_path / "unused.sqlite", manifest=manifest, split_name="train")


def test_source_agnostic_quality_report_detects_non_sqlite_candle_defects() -> None:
    snapshot = DatasetSnapshot(
        snapshot_id="quality_non_sqlite",
        source="csv_fixture",
        market="KRW-BTC",
        interval="1m",
        split_name="train",
        date_range=DateRange(start="2023-01-01", end="2023-01-01"),
        candles=(
            Candle(1_672_531_200_000, 100.0, 101.0, 99.0, 100.0, 1.0),
            Candle(1_672_531_200_000, 100.0, 99.0, 101.0, 0.0, -1.0),
        ),
    )

    report = _build_source_agnostic_dataset_quality_report(
        db_path=None,
        snapshot=snapshot,
        adapter_name="csv_fixture_adapter",
        adapter_version="1",
        adapter_provenance={"csv": {"path": "memory"}},
    )

    assert report.quality_gate_status == "FAIL"
    assert "duplicate_candle_keys" in report.quality_gate_reasons
    assert "ohlc_invariant_violation" in report.quality_gate_reasons
    assert "non_positive_price" in report.quality_gate_reasons
    assert "negative_volume" in report.quality_gate_reasons
    assert report.payload["adapter_provenance"] == {"csv": {"path": "memory"}}
    assert report.payload["db_schema_fingerprint"] is None


def test_production_bound_adapter_provenance_requires_source_hashes() -> None:
    manifest = replace(_manifest("unit_candles_adapter_source"), deployment_tier="paper_candidate")
    snapshot = DatasetSnapshot(
        snapshot_id="quality_non_sqlite",
        source="unit_candles_adapter_source",
        market="KRW-BTC",
        interval="1m",
        split_name="train",
        date_range=DateRange(start="2023-01-01", end="2023-01-01"),
        candles=(Candle(1_672_531_200_000, 100.0, 101.0, 99.0, 100.0, 1.0),),
    )
    report = _build_source_agnostic_dataset_quality_report(
        db_path=None,
        snapshot=snapshot,
        adapter_name="unit_candle_adapter",
        adapter_version="1",
        adapter_provenance={"unit": {"source": "unit_candles_adapter_source"}},
    )
    report.payload["source_content_hash"] = "missing:unit"
    report.payload["source_schema_hash"] = "not_applicable:unit"
    report.payload["content_hash"] = "sha256:test"

    with pytest.raises(ResearchValidationError, match="dataset_adapter_provenance_failed:.*source_content_hash_missing"):
        _validate_dataset_adapter_provenance(manifest=manifest, quality_reports={"train": report})


def test_production_bound_adapter_provenance_rejects_mutable_locator() -> None:
    manifest = replace(_manifest("unit_candles_adapter_source"), deployment_tier="paper_candidate")
    manifest = replace(
        manifest,
        dataset=replace(manifest.dataset, source_uri="latest"),
    )
    snapshot = DatasetSnapshot(
        snapshot_id="quality_non_sqlite",
        source="unit_candles_adapter_source",
        market="KRW-BTC",
        interval="1m",
        split_name="train",
        date_range=DateRange(start="2023-01-01", end="2023-01-01"),
        candles=(Candle(1_672_531_200_000, 100.0, 101.0, 99.0, 100.0, 1.0),),
    )
    report = _build_source_agnostic_dataset_quality_report(
        db_path=None,
        snapshot=snapshot,
        adapter_name="unit_candle_adapter",
        adapter_version="1",
        adapter_provenance={"unit": {"source": "unit_candles_adapter_source"}},
    )
    report.payload["source_schema_hash"] = "sha256:schema"
    report.payload["content_hash"] = "sha256:test"

    with pytest.raises(ResearchValidationError, match="mutable_dataset_locator"):
        _validate_dataset_adapter_provenance(manifest=manifest, quality_reports={"train": report})


def test_production_bound_adapter_provenance_rejects_declared_hash_mismatch() -> None:
    manifest = replace(_manifest("unit_candles_adapter_source"), deployment_tier="paper_candidate")
    manifest = replace(
        manifest,
        dataset=replace(manifest.dataset, source_content_hash="sha256:declared-other"),
    )
    snapshot = DatasetSnapshot(
        snapshot_id="quality_non_sqlite",
        source="unit_candles_adapter_source",
        market="KRW-BTC",
        interval="1m",
        split_name="train",
        date_range=DateRange(start="2023-01-01", end="2023-01-01"),
        candles=(Candle(1_672_531_200_000, 100.0, 101.0, 99.0, 100.0, 1.0),),
    )
    report = _build_source_agnostic_dataset_quality_report(
        db_path=None,
        snapshot=snapshot,
        adapter_name="unit_candle_adapter",
        adapter_version="1",
        adapter_provenance={"unit": {"source": "unit_candles_adapter_source"}},
    )
    report.payload["source_schema_hash"] = "sha256:schema"
    report.payload["content_hash"] = "sha256:test"

    with pytest.raises(ResearchValidationError, match="source_content_hash_mismatch"):
        _validate_dataset_adapter_provenance(manifest=manifest, quality_reports={"train": report})
