"""Versioned scientific contract kept separate from the changing operational plan."""
import math


def validate_contract(spec):
    contract = spec.get("contract", {})
    required = {"id", "version", "mode", "definitions", "assumptions", "exclusions", "novelty",
                "deliverables", "acceptance_authority", "verifier_authority", "required_checks", "acceptance_scope"}
    if required - contract.keys() or type(contract["version"]) is not int or contract["version"] < 1:
        raise ValueError("Complete the versioned problem contract before registration")
    if contract["mode"] not in ("computational", "mathematics", "literature", "empirical", "mixed"):
        raise ValueError("Unknown research mode")
    if contract["novelty"] not in ("required", "optional", "not_required") or not contract["required_checks"]:
        raise ValueError("Specify novelty and required claim checks")
    for key in ("id", "acceptance_authority", "verifier_authority", "acceptance_scope"):
        if not isinstance(contract[key], str) or not contract[key].strip():
            raise ValueError("Contract authority and scope fields must be explicit")
    runtime = spec.get("runtime", {})
    required = {"topology", "coordinator_slots", "max_explorers", "max_delegation_depth", "lease_seconds",
                "max_attempts", "shutdown_seconds", "reporting_seconds", "allowed_tools", "models",
                "permission_boundary", "budget_enforcement", "budget_units", "allocations", "operation_units"}
    if required - runtime.keys():
        raise ValueError("Complete the runtime capability and resource contract")
    if runtime["topology"] not in ("single", "independent", "coordinator"):
        raise ValueError("Unsupported topology")
    for key in ("coordinator_slots", "max_explorers", "max_delegation_depth", "lease_seconds", "max_attempts", "shutdown_seconds", "reporting_seconds"):
        if type(runtime[key]) is not int or runtime[key] < (0 if key in ("coordinator_slots", "max_delegation_depth") else 1):
            raise ValueError("Runtime limits must be explicit nonnegative/positive integers")
    if runtime["max_explorers"] + runtime["coordinator_slots"] > spec["budget"]["max_agent_tasks"]:
        raise ValueError("Explorer and coordinator slots exceed the total concurrency cap")
    if runtime["topology"] == "single" and (runtime["max_explorers"] != 1 or runtime["coordinator_slots"] != 0):
        raise ValueError("Single-agent topology has one worker and no additional coordinator slot")
    if runtime["shutdown_seconds"] + runtime["reporting_seconds"] >= spec["budget"]["wall_seconds"]:
        raise ValueError("Reserve time while leaving a positive research window")
    if runtime["budget_enforcement"] not in ("best_effort", "local_credits_only"):
        raise ValueError("No hard provider spending guarantee is implemented; declare best effort or local credits")
    if runtime["permission_boundary"] not in ("unconfigured", "host_enforced"):
        raise ValueError("Declare the actual permission boundary")
    if set(runtime["allocations"]) != {"research", "verification", "reporting"}:
        raise ValueError("Reserve separate research, verification and reporting budgets")
    for units in [*runtime["allocations"].values(), runtime["operation_units"]]:
        if type(units) not in (int, float) or not math.isfinite(units) or units <= 0:
            raise ValueError("Budget allocations and reservation bounds must be positive finite values")


def scope_instruction(mode):
    instructions = {
        "computational": "Audit actual code, data, split, baseline fairness, seeds, all attempted/excluded runs, and leakage. Reproduction alone does not establish generalization.",
        "mathematics": "Preserve quantifiers, definitions, assumptions, boundary conditions and permitted axioms. Check statement meaning as well as proof acceptance. Numerical evidence is not proof.",
        "literature": "Retrieve primary sources and exact locators. Record conflicting evidence, shared origins, dates and missing access. Do not infer exhaustive coverage from an incomplete search.",
        "empirical": "Check controls, calibration, uncertainty and measurement validity. Separate proposed experiments from completed measurements and require configured domain review.",
        "mixed": "Apply separate domain checks and accepted scopes to each mathematical, computational, literature or empirical output.",
    }
    return instructions[mode]
