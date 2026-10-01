from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .engine import evaluate
from .io import load_records, write_json
from .report import render_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run frozen V5B, V8, and V10 research")
    sub = parser.add_subparsers(dest="command", required=True)
    history = sub.add_parser("history", help="score normalized historical JSON/JSONL")
    history.add_argument("--input", default="data/history.jsonl")
    history.add_argument("--output-dir", default="artifacts/offline")
    current = sub.add_parser("snapshot", help="fetch one current read-only Kalshi/weather snapshot")
    current.add_argument("--date", default=None, help="market date YYYY-MM-DD; default current/next open")
    current.add_argument("--output-dir", default="artifacts/online")
    current.add_argument("--api-base", default="https://external-api.kalshi.com/trade-api/v2")
    watch = sub.add_parser("watch", help="refresh the read-only current-data dashboard")
    watch.add_argument("--date", default=None, help="market date YYYY-MM-DD; default current/next open")
    watch.add_argument("--output-dir", default="artifacts/online")
    watch.add_argument("--api-base", default="https://external-api.kalshi.com/trade-api/v2")
    watch.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if args.command == "history":
        result = evaluate(load_records(args.input))
        output = Path(args.output_dir)
        result_path = write_json(output / "results.json", result)
        report_path = render_report(result, output / "report.html", title="KLAX Offline V5B · V8 · V10")
        print(f"Results: {result_path.resolve()}")
        print(f"Graphic: {report_path.resolve()}")
        print("Forecast ranking: " + " > ".join(result["forecast_ranking"]))
        print("Return ranking: " + (" > ".join(result["return_ranking"]) or "no executable trades"))
        return 0
    if args.command in {"snapshot", "watch"}:
        from .online import snapshot, watch as run_watch
        target = date.fromisoformat(args.date) if args.date else None
        if args.command == "watch":
            run_watch(target=target, output_dir=args.output_dir, api_base=args.api_base, interval_seconds=args.interval_seconds)
            return 0
        result = snapshot(target=target, output_dir=args.output_dir, api_base=args.api_base)
        print(f"Snapshot: {(Path(args.output_dir) / 'latest.json').resolve()}")
        print(f"Graphic: {(Path(args.output_dir) / 'dashboard.html').resolve()}")
        print("Orders attempted: 0")
        print("Actions: " + ", ".join(f"{name}={result['scored']['models'][name]['decision']['status']}" for name in ("V5B", "V8", "V10")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

