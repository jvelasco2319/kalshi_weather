from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from kalshi_weather.backtest.replay import replay_snapshots
from kalshi_weather.data.storage import SQLiteStore


def test_store_writes_json_snapshot_and_replay_counts_it() -> None:
    base = Path(".test-artifacts") / f"storage-replay-{uuid4().hex}"
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_snapshot(
            "paper_once",
            {
                "probabilities": {"T": 0.8},
                "orderbook_tops": {
                    "T": {
                        "yes_bid": "0.50",
                        "yes_ask": "0.55",
                        "no_bid": "0.45",
                        "no_ask": "0.50",
                    }
                },
            },
        )

        result = replay_snapshots(base / "snapshots")

        assert result["snapshot_count"] == 1
        assert result["trade_count"] == 1
        assert Decimal(result["net_pnl"]) < 0
    finally:
        rmtree(base, ignore_errors=True)
