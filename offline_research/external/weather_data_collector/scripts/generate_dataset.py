"""Command line entry point for the complete daily-high forecast pipeline."""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import sys

# Permit both ``python -m scripts.generate_dataset`` and direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_pipeline import run_date_range, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and process GFS, GFS Seamless, NAM, and NBM daily-high forecasts.")
    parser.add_argument("--city", required=True, help="City name, e.g. 'Los Angeles, CA'")
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--date", help="One target local date, YYYY-MM-DD")
    dates.add_argument("--start-date", help="First target local date in an inclusive range, YYYY-MM-DD")
    parser.add_argument("--end-date", help="Last target local date in an inclusive range, YYYY-MM-DD; requires --start-date")
    parser.add_argument("--init", help="Exact UTC initialization for a single --date, YYYY-MM-DDTHH")
    parser.add_argument("--init-hour", type=int, default=0, help="UTC initialization hour for each range day (default: 0)")
    parser.add_argument("--strict", action="store_true", help="Fail instead of writing a partial result when an archive is unavailable")
    args = parser.parse_args()
    if args.start_date:
        if not args.end_date:
            parser.error("--end-date is required with --start-date")
        if args.init:
            parser.error("--init is only valid with --date; use --init-hour for a date range")
        run_date_range(
            args.city,
            date.fromisoformat(args.start_date),
            date.fromisoformat(args.end_date),
            args.init_hour,
            strict=args.strict,
        )
        return
    if args.end_date:
        parser.error("--end-date requires --start-date")
    target_date = date.fromisoformat(args.date)
    initialization = datetime.fromisoformat(args.init).replace(tzinfo=timezone.utc) if args.init else datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    run_pipeline(args.city, target_date, initialization, strict=args.strict)


if __name__ == "__main__":
    main()
