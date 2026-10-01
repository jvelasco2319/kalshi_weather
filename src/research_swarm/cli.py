import argparse
import json
from .campaign import confirm, freeze, initialize, pause, reflect, request_reflection, resume, review, run_epoch, status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded research controller; real agents supply review packets")
    parser.add_argument("action", choices=("init", "epoch", "review", "status", "freeze", "confirm", "pause", "resume", "reflection", "reflect"))
    parser.add_argument("--run-dir", default="runs/demo")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--spec", default="config/example_problem.json")
    parser.add_argument("--packet")
    parser.add_argument("--candidate-id")
    parser.add_argument("--confirmation-data")
    parser.add_argument("--authorize-one-shot", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "init":
        value = initialize(args.project_root, args.spec, args.run_dir)
    elif args.action == "epoch":
        value = run_epoch(args.run_dir)
    elif args.action == "pause":
        value = pause(args.run_dir)
    elif args.action == "resume":
        value = resume(args.run_dir)  # Never resets registration, deadline, or counters.
    elif args.action == "reflection":
        value = request_reflection(args.run_dir)
    elif args.action == "reflect":
        if not args.packet:
            parser.error("--packet is required")
        value = reflect(args.run_dir, args.packet)
    elif args.action == "review":
        if not args.packet:
            parser.error("--packet is required")
        value = review(args.run_dir, args.packet)
    elif args.action == "freeze":
        if not args.candidate_id:
            parser.error("--candidate-id is required")
        value = freeze(args.run_dir, args.candidate_id)
    elif args.action == "confirm":
        if not args.confirmation_data:
            parser.error("--confirmation-data is required")
        value = confirm(args.run_dir, args.confirmation_data, args.authorize_one_shot)
    else:
        value = status(args.run_dir)
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
