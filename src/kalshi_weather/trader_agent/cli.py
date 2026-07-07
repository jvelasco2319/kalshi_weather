from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .agent import TraderAgent
from .context_builder import build_context_from_inputs
from .journal import JsonlTraderJournal
from .llm_client import DryRunTraderLLMClient, MockTraderLLMClient
from .trader_types import MarketBracket, ModelEstimate, ProbabilityBin, RiskLimits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM trader-agent demo commands")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ["trader-context", "trader-recommend"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--series", default="KXHIGHLAX")
        cmd.add_argument("--station", default="KLAX")
        cmd.add_argument("--target-date", default=None)
        cmd.add_argument("--llm-provider", choices=["dry-run", "mock"], default="dry-run")
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--output", default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    context = _demo_context(args.series, args.station, args.target_date)

    if args.command == "trader-context":
        payload = context.to_dict()
    elif args.command == "trader-recommend":
        llm = MockTraderLLMClient() if args.llm_provider == "mock" else DryRunTraderLLMClient()
        journal = JsonlTraderJournal(Path("reports") / "trader_agent_runs.jsonl")
        result = TraderAgent(llm_client=llm, journal=journal).recommend(context)
        payload = result.to_dict()
    else:
        raise AssertionError(f"unknown command {args.command}")

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


def _demo_context(series: str, station: str, market_date: str | None):
    """Demo context for the standalone package.

    Codex should replace this with real calls into existing Kalshi/weather code
    when integrating into the repo's main CLI.
    """
    probs = [
        ProbabilityBin("65 or below", 0.003, None, 65),
        ProbabilityBin("66-67", 0.018, 66, 67),
        ProbabilityBin("68-69", 0.135, 68, 69),
        ProbabilityBin("70-71", 0.540, 70, 71),
        ProbabilityBin("72-73", 0.275, 72, 73),
        ProbabilityBin("74+", 0.029, 74, None),
    ]
    brackets = [
        MarketBracket(series, f"{series}-T65", "65 or below", None, 65, yes_bid_cents=0, yes_ask_cents=1, volume=10),
        MarketBracket(series, f"{series}-T66-T67", "66-67", 66, 67, yes_bid_cents=1, yes_ask_cents=3, volume=25),
        MarketBracket(series, f"{series}-T68-T69", "68-69", 68, 69, yes_bid_cents=7, yes_ask_cents=8, volume=300),
        MarketBracket(series, f"{series}-T70-T71", "70-71", 70, 71, yes_bid_cents=58, yes_ask_cents=59, volume=1200),
        MarketBracket(series, f"{series}-T72-T73", "72-73", 72, 73, yes_bid_cents=34, yes_ask_cents=35, volume=800),
        MarketBracket(series, f"{series}-T74", "74+", 74, None, yes_bid_cents=3, yes_ask_cents=4, volume=150),
    ]
    return build_context_from_inputs(
        series=series,
        station=station,
        market_date=market_date,
        model_estimates=[ModelEstimate("demo_blend", 71.1, weight=1.0)],
        probability_bins=probs,
        market_brackets=brackets,
        risk_limits=RiskLimits(min_edge_cents=3.0, max_contracts_per_trade=100, max_risk_dollars_per_trade=50),
        observed_high_so_far_f=69.0,
        weather_notes="Demo context only. Replace with repo weather/model providers.",
        market_notes="Demo market only. Replace with Kalshi client data.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
