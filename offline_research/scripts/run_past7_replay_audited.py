from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from past7_replay.audited import build_audited_freeze, score_audited_freeze


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the audited causal seven-day replay")
    parser.add_argument("phase", choices=("predict", "score", "run"))
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--replay-root")
    args = parser.parse_args()
    root = Path(args.project_root)
    replay = None if args.replay_root is None else Path(args.replay_root)
    if args.phase in {"predict", "run"}:
        value = build_audited_freeze(root, replay)
        print({
            "prediction_status": value["status"],
            "target_date_count": len(value["target_dates"]),
            "method_count": len(value["methods"]),
            "outcomes_read": value["outcomes_read"],
        })
    if args.phase in {"score", "run"}:
        value = score_audited_freeze(root, replay)
        print({
            "scoring_status": value["status"],
            "method_count": len(value["summaries"]),
            "outcomes_read_after_prediction_freeze": value["outcomes_read_after_prediction_freeze"],
        })


if __name__ == "__main__":
    main()

