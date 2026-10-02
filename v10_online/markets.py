"""Current KLAX bracket identity and read-only book interpretation."""
from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import math
import re
from urllib.parse import urlencode

from .transport import KALSHI

MONTHS = ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")


def event_ticker(day: str) -> str:
    d = date.fromisoformat(day)
    return f"KXHIGHLAX-{d.year%100:02d}{MONTHS[d.month-1]}{d.day:02d}"


def _integer(value):
    if isinstance(value,bool):
        raise ValueError("Boolean strike")
    number = Decimal(str(value))
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("V10 requires integer temperature bracket strikes")
    return int(number)


def canonical_contracts(markets: list[dict], day: str, settlement_source: str) -> list[dict]:
    if len(markets) != 6 or len({m.get("ticker") for m in markets}) != 6:
        raise ValueError("Exactly six distinct KLAX brackets are required")
    if settlement_source not in ("The Weather Company","National Weather Service Climatological Report (Daily)"):
        raise ValueError("Unrecognized settlement source")
    rows=[]
    for market in markets:
        rules = market.get("rules_primary", "")
        if (market.get("event_ticker") != event_ticker(day) or market.get("market_type") != "binary"
            or not str(market.get("ticker","")).startswith(event_ticker(day)+"-")
            or "clilax" not in rules.casefold() or "maximum temperature" not in rules.casefold()
            or settlement_source.casefold().split(" climatological")[0] not in rules.casefold()):
            raise ValueError("KLAX contract event/station/source identity differs")
        strike=market.get("strike_type")
        floor=None if market.get("floor_strike") is None else _integer(market["floor_strike"])
        cap=None if market.get("cap_strike") is None else _integer(market["cap_strike"])
        if strike == "less" and floor is None and cap is not None:
            lower,upper=None,cap-1
        elif strike == "greater" and cap is None and floor is not None:
            lower,upper=floor+1,None
        elif strike == "between" and floor is not None and cap == floor+1:
            lower,upper=floor,cap
        else:
            raise ValueError("Contract shape differs from V10's six integer-outcome brackets")
        target=date.fromisoformat(day)
        date_text=f"{MONTHS[target.month-1].title()} {target.day}, {target.year}"
        source_text="The Weather Company" if settlement_source == "The Weather Company" else "National Weather Service"
        prefix=f"If the maximum temperature recorded at Los Angeles (CLILAX) for {date_text}, is "
        criterion = f"less than {cap}" if strike=="less" else f"greater than {floor}" if strike=="greater" else f"between {floor}-{cap}"
        expected=prefix+criterion+chr(176)+" fahrenheit according to "+source_text+", then the market resolves to Yes."
        if " ".join(rules.split()).casefold() != expected.casefold():
            raise ValueError("Primary rule date, source or temperature criterion differs from metadata")
        rows.append({"market_ticker":market["ticker"], "ticker":market["ticker"],
            "climate_date":day,"event_ticker":event_ticker(day), "market_type":"binary",
            "strike_type":strike,"floor_strike":floor,"cap_strike":cap,
            "lower_bound_f":lower,"upper_bound_f":upper,"settlement_source":settlement_source,
            "rules_primary":rules,"rules_secondary":market.get("rules_secondary",""),
            "legacy_v10_cdf_interpretation":"integer temperature labels with half-degree CDF boundaries"})
    rows.sort(key=lambda r:-math.inf if r["lower_bound_f"] is None else r["lower_bound_f"])
    if rows[0]["strike_type"] != "less" or rows[-1]["strike_type"] != "greater" or any(r["strike_type"] != "between" for r in rows[1:-1]):
        raise ValueError("Six-bracket partition shape differs")
    for a,b in zip(rows,rows[1:]):
        if a["upper_bound_f"] is None or b["lower_bound_f"] != a["upper_bound_f"]+1:
            raise ValueError("Temperature bracket partition has a gap or overlap")
    return rows


def normalize_book(value: dict) -> dict:
    book=value.get("orderbook_fp")
    if isinstance(book,dict):
        yes,no=book.get("yes_dollars",[]),book.get("no_dollars",[])
        factor=Decimal(1)
    elif isinstance(value.get("orderbook"),dict):
        book=value["orderbook"];yes,no=book.get("yes",[]),book.get("no",[])
        factor=Decimal(100)
    else:
        raise ValueError("Orderbook schema differs")
    def side(levels):
        checked=[]
        for item in levels or []:
            if not isinstance(item,list) or len(item)!=2:
                raise ValueError("Orderbook level shape differs")
            price,size=Decimal(str(item[0]))/factor,Decimal(str(item[1]))
            if not price.is_finite() or not size.is_finite() or not 0<=price<=1 or size<=0:
                raise ValueError("Orderbook price/size differs")
            checked.append((price,size))
        return max(checked,default=None,key=lambda p:p[0])
    y,n=side(yes),side(no)
    bid=None if y is None else y[0]
    ask=None if n is None else 1-n[0]
    if bid is not None and ask is not None and bid>ask:
        raise ValueError("Crossed binary book")
    return {"yes_bid":None if bid is None else float(bid),
        "yes_ask":None if ask is None else float(ask),
        "yes_bid_size":None if y is None else float(y[1]),
        "yes_ask_size":None if n is None else float(n[1]),
        "yes_midpoint":None if bid is None or ask is None else float((bid+ask)/2),
        "evidence":"PUBLIC_REST_SNAPSHOT_ONLY", "fill_demonstrated":False}


def collect_markets(client, day: str, include_books=True) -> dict:
    series=client.get_json(KALSHI+"/series/KXHIGHLAX").get("series",{})
    if series.get("ticker") != "KXHIGHLAX":
        raise ValueError("Series identity differs")
    sources=series.get("settlement_sources",[])
    source="The Weather Company" if any(s.get("name")=="The Weather Company" for s in sources) else "National Weather Service Climatological Report (Daily)" if any("National Weather Service" in s.get("name","") for s in sources) else None
    raw=client.get_json(KALSHI+"/markets?"+urlencode({"event_ticker":event_ticker(day),"limit":100}))
    if raw.get("cursor"):
        raise ValueError("Unexpected paginated six-bracket universe")
    original=raw.get("markets",[])
    contracts=canonical_contracts(original,day,source)
    # Inspect the response for outcomes rather than merely dropping them.
    if any(m.get("result") not in (None,"") or m.get("expiration_value") not in (None,"") or m.get("status") not in ("active","open") for m in original):
        raise ValueError("A prospective universe is closed or contains outcome information")
    quotes=[]
    if include_books:
        for c in contracts:
            before=len(client.receipts)
            book=normalize_book(client.get_json(KALSHI+f'/markets/{c["ticker"]}/orderbook?depth=10'))
            if len(client.receipts)!=before+1:
                raise ValueError("Missing book retrieval receipt")
            quotes.append({"market_ticker":c["ticker"], **book,
                "retrieved_at_utc":client.receipts[-1]["retrieved_at_utc"]})
    mids=[r["yes_midpoint"] for r in quotes]
    proxy=None
    if len(mids)==6 and all(p is not None for p in mids) and sum(mids)>0:
        proxy=[p/sum(mids) for p in mids]
    return {"contracts":contracts,"series":series,"settlement_source":source,"quotes":quotes,
        "market_probability_proxy":proxy, "market_proxy_definition":"Normalize six contemporaneous YES bid/ask midpoints; REST snapshot proxy, not executable fills or a calibrated market distribution",
        "orders":0}
