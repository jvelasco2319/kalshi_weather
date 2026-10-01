"""Run the locked September 21-27 replay without network or orders."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from past7_replay import build_prediction_freeze, score_prediction_freeze


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("predict", "score", "run"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--replay-root", type=Path)
    args = parser.parse_args(argv)
    replay = args.replay_root
    if args.action in {"predict", "run"}:
        freeze = build_prediction_freeze(args.project_root, replay)
        print({
            "prediction_status": freeze["status"],
            "target_date_count": len(freeze["target_dates"]),
            "method_count": len(freeze["methods"]),
            "outcomes_read": freeze["outcomes_read"],
        })
    if args.action in {"score", "run"}:
        result = score_prediction_freeze(args.project_root, replay)
        print({
            "scoring_status": result["status"],
            "target_date_count": len(result["target_dates"]),
            "outcomes_read_after_prediction_freeze": result["outcomes_read_after_prediction_freeze"],
        })


if __name__ == "__main__":
    main()
