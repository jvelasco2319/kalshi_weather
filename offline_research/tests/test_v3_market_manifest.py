"""Development-only V3 minute-market evidence tests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from klax_lab.acquire_kalshi import build_v3_development_market_manifest


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def test_v3_market_manifest_verifies_development_without_protected_bytes(tmp_path):
    root = tmp_path
    raw = b'{"ok":true}'
    candle_digest = hashlib.sha256(raw).hexdigest()
    trade_raw = b'{"trades":[]}'
    trade_digest = hashlib.sha256(trade_raw).hexdigest()
    candle_path = "data/raw/kalshi/candle.json"
    trade_path = "data/raw/kalshi/trade.json"
    (root / candle_path).parent.mkdir(parents=True, exist_ok=True)
    (root / candle_path).write_bytes(raw)
    (root / trade_path).write_bytes(trade_raw)
    ticker = "KXHIGHLAX-25JAN05-T70"
    _write_json(root / "data/manifests/kalshi_coverage.json", {"contracts": [{
        "ticker": ticker, "climate_date": "2025-01-05",
        "station_identity_screen": True, "status": "finalized",
    }, {
        "ticker": "KXHIGHLAX-25JUL01-T70", "climate_date": "2025-07-01",
        "station_identity_screen": True, "status": "finalized",
    }]})
    _write_json(root / "data/manifests/kalshi_downloads.json", {"sources": {
        "candle": {"path": candle_path, "sha256": candle_digest, "bytes": len(raw),
                   "url": "https://api.elections.kalshi.com/trade-api/v2/series/x/markets/y/candlesticks?period_interval=1"},
        "trade": {"path": trade_path, "sha256": trade_digest, "bytes": len(trade_raw),
                  "url": "https://api.elections.kalshi.com/trade-api/v2/historical/trades?ticker=x"},
    }})
    _write_json(root / "data/manifests/kalshi_candles_1m_2025-01-05_2025-01-05.json", {
        "period_interval": 1, "contracts": [{"ticker": ticker, "climate_date": "2025-01-05",
        "status": "downloaded", "path": candle_path, "source_sha256": candle_digest, "rows": 4}],
    })
    _write_json(root / "data/manifests/kalshi_trades_2025-01-05_2025-01-05.json", {
        "contracts": [{"ticker": ticker, "climate_date": "2025-01-05", "status": "empty",
        "pages": [{"path": trade_path, "source_sha256": trade_digest, "rows": 0}], "rows": 0}],
    })
    report = build_v3_development_market_manifest(root)
    assert report["status"] == "DEVELOPMENT_COMPLETE"
    assert report["protected_final_read"] is False
    assert report["one_minute_candle_contracts"] == 1
    assert report["public_trade_contracts"] == 1
    assert (root / "data/manifests/v3_minute_kalshi.json").is_file()
