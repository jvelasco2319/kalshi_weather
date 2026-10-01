import argparse
import json
from pathlib import Path
from .campaign import confirm, freeze, initialize, pause, reflect, refresh_report, request_reflection, resume, review, run_epoch, status
from .artifacts import read
from . import evidence, evaluation, planning, resources, tasks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded research controller; real agents supply review packets")
    extra = ("enqueue", "claim-task", "start-task", "submit-task", "ack-task", "release-task", "cancel-task", "tasks", "resources",
             "reconcile-usage", "transfer-budget", "source", "remember", "retrieve", "check-record", "decision", "quarantine", "revalidate",
             "plan", "finish", "evaluate-architecture", "claim", "review-claim", "claims", "confirm-stop", "report")
    parser.add_argument("action", choices=("init", "epoch", "review", "status", "freeze", "confirm", "pause", "resume", "reflection", "reflect", *extra))
    parser.add_argument("--run-dir", default="runs/demo")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--spec", default="config/example_problem.json")
    parser.add_argument("--packet")
    parser.add_argument("--candidate-id")
    parser.add_argument("--confirmation-data")
    parser.add_argument("--authorize-one-shot", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--owner")
    parser.add_argument("--attempt-id")
    parser.add_argument("--fence", type=int)
    parser.add_argument("--result-id")
    parser.add_argument("--reservation-id")
    parser.add_argument("--actual-units", type=float)
    parser.add_argument("--conservative", action="store_true")
    parser.add_argument("--scope", choices=("project", "method"))
    parser.add_argument("--reason")
    parser.add_argument("--error-kind", choices=("transient", "semantic"), default="semantic")
    parser.add_argument("--authorize-decision", action="store_true")
    parser.add_argument("--evaluation-files", nargs="+")
    args = parser.parse_args(argv)
    directory = Path(args.run_dir).resolve()
    packet_actions = {"enqueue", "submit-task", "source", "remember", "check-record", "decision", "quarantine", "revalidate", "plan", "finish", "transfer-budget", "claim", "review-claim", "confirm-stop"}
    if args.action in packet_actions and not args.packet:
        parser.error("--packet is required")
    packet = read(args.packet) if args.action in packet_actions else None
    if args.action in extra:
        if args.action == "enqueue": value = tasks.enqueue(directory, packet)
        elif args.action == "claim-task": value = tasks.claim(directory, args.task_id, args.owner)
        elif args.action == "start-task": value = tasks.start(directory, args.task_id, args.attempt_id, args.fence, args.owner)
        elif args.action == "submit-task": value = tasks.submit(directory, packet)
        elif args.action == "ack-task": value = tasks.acknowledge(directory, args.result_id)
        elif args.action in ("release-task", "cancel-task"): value = tasks.release(directory, args.task_id, cancel=args.action == "cancel-task", error_kind=args.error_kind, reason=args.reason)
        elif args.action == "tasks": value = tasks.task_status(directory)
        elif args.action == "resources": value = resources.budget_status(directory)
        elif args.action == "reconcile-usage": value = resources.reconcile(directory, args.reservation_id, args.actual_units, conservative=args.conservative)
        elif args.action == "transfer-budget": value = resources.transfer(directory, packet["source"], packet["target"], packet["units"], packet["reason"])
        elif args.action == "source": value = evidence.source_record(directory, packet)
        elif args.action == "remember": value = evidence.remember(directory, packet)
        elif args.action == "retrieve": value = evidence.retrieve(directory, args.scope)
        elif args.action == "check-record": value = evidence.import_check(directory, packet)
        elif args.action == "decision": value = evidence.decide(directory, packet, authorized=args.authorize_decision)
        elif args.action == "quarantine": value = evidence.quarantine(directory, packet)
        elif args.action == "revalidate": value = evidence.revalidate(directory, packet, authorized=args.authorize_decision)
        elif args.action == "plan": value = planning.update_plan(directory, packet)
        elif args.action == "finish": value = planning.finish(directory, packet)
        elif args.action == "claim": value = evidence.propose_claim(directory, packet)
        elif args.action == "review-claim": value = evidence.review_claim(directory, packet)
        elif args.action == "claims": value = evidence.claim_index(directory)
        elif args.action == "confirm-stop": value = tasks.confirm_stop(directory, args.attempt_id, packet)
        elif args.action == "report": value = refresh_report(directory)
        else: value = evaluation.compare(args.evaluation_files or [])
    elif args.action == "init":
        value = initialize(args.project_root, args.spec, args.run_dir)
    elif args.action == "epoch":
        value = run_epoch(args.run_dir)
    elif args.action == "pause":
        value = pause(args.run_dir)
    elif args.action == "resume":
        value = resume(args.run_dir)  # Never resets registration, deadline, or counters.
    elif args.action == "reflection":
        value = request_reflection(args.run_dir, read(args.packet) if args.packet else None)
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
