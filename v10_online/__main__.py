"""Run the read-only V10 online monitor."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from . import runner


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root",default=str(Path(__file__).resolve().parents[1]))
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("register","report","refresh"):
        commands.add_parser(name)
    for name in ("stage","capture","preview","settle","run"):
        sub=commands.add_parser(name)
        sub.add_argument("--date",default=datetime.now(timezone.utc).date().isoformat())
        if name in ("capture","run"):
            sub.add_argument("--wait-seconds",type=int,default=1200)
    args=parser.parse_args()
    root=Path(args.project_root).resolve()
    try:
        if args.command in ("register","report","refresh"):
            result=getattr(runner,args.command)(root)
        elif args.command in ("capture","run"):
            result=getattr(runner,args.command)(root,args.date,wait_seconds=args.wait_seconds)
        else:
            result=getattr(runner,args.command)(root,args.date)
        if args.command in ("preview","capture","run") and "prediction" in result:
            market=result["market"];model=result["prediction"]
            print(result["status"])
            print("Temperature bracket                       V10 probability   YES bid   YES ask")
            quote={q["market_ticker"]:q for q in market["quotes"]}
            for c,p in zip(market["contracts"],model["probabilities"]):
                q=quote[c["ticker"]]
                def show(value):return "missing" if value is None else f"{value:.4f}"
                print(f'{c["ticker"]:<43} {100*p:6.2f}%       {show(q["yes_bid"]):>7}   {show(q["yes_ask"]):>7}')
            print("Settlement source:",market["settlement_source"])
            print("Orders placed: 0. This is a forecast record and market snapshot.")
            print("Current settlement rules differ from the original NWS training target.")
        else:
            compact={k:v for k,v in result.items() if k not in ("input_bindings","weather","capture_receipts","raw_bindings")}
            print(json.dumps(compact,indent=2,allow_nan=False))
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({"status":"STOPPED_WITHOUT_FORECAST", "reason":str(exc), "orders":0}))
        raise SystemExit(1)


if __name__=="__main__":main()
