from __future__ import annotations

import argparse
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
    args = parser.parse_args(argv)
    if args.command == "history":
        result = evaluate(load_records(args.input))
        output = Path(args.output_dir)
        result_path = write_json(output / "results.json", result)
        report_path = render_report(result, output / "report.html", title="KLAX Offline V5B · V8 · V10")
        print(f"Results: {result_path.resolve()}")
        print(f"Graphic: {report_path.resolve()}")
        print("Ranking: " + " > ".join(result["ranking"]))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

