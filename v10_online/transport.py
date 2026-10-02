"""Bounded public HTTPS GET transport with immutable receipts and no auth."""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from .storage import write_immutable

KALSHI = "https://external-api.kalshi.com/trade-api/v2"


def validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None,443) or parts.fragment:
        raise ValueError("Only credential-free public HTTPS URLs are permitted")
    if any(x in parts.path.lower() for x in ("%", "..", "\\")):
        raise ValueError("Encoded or traversing paths are denied")
    host, path = parts.hostname, parts.path
    if host == "external-api.kalshi.com":
        patterns = (r"/trade-api/v2/series/KXHIGHLAX", r"/trade-api/v2/markets",
            r"/trade-api/v2/markets/KXHIGHLAX-[A-Z0-9.\-]+(?:/orderbook)?")
        allowed = any(re.fullmatch(p,path) for p in patterns)
    elif host in ("noaa-hrrr-bdp-pds.s3.amazonaws.com", "noaa-gefs-pds.s3.amazonaws.com"):
        allowed = bool(re.fullmatch(r"/(?:hrrr|gefs)\.\d{8}/[A-Za-z0-9_./\-]+",path))
    elif host == "mesonet.agron.iastate.edu":
        allowed = path == "/cgi-bin/request/asos.py"
    elif host == "assets.kalshi.com":
        allowed = path == "/contract_terms/GLOBALTEMPERATURE.pdf"
    else:
        allowed = False
    if not allowed:
        raise ValueError("URL is outside the public weather/market read allowlist")
    query=parse_qs(parts.query,keep_blank_values=True)
    if host == "external-api.kalshi.com":
        if path=="/trade-api/v2/markets":
            if set(query)!={"event_ticker","limit"} or len(query["event_ticker"])!=1 or not re.fullmatch(r"KXHIGHLAX-\d{2}[A-Z]{3}\d{2}",query["event_ticker"][0]) or query["limit"]!=["100"]:
                raise ValueError("Only one bounded KLAX event query is permitted")
        elif path.endswith("/orderbook"):
            if query not in ({},{"depth":["10"]}):
                raise ValueError("Only a bounded public orderbook query is permitted")
        elif query:
            raise ValueError("Unexpected public Kalshi query")
    elif host != "mesonet.agron.iastate.edu" and query:
        raise ValueError("Unexpected weather/source URL query")
    elif host == "mesonet.agron.iastate.edu":
        keys={"station","data","sts","ets","tz","format","latlon","elev","missing","trace","direct","report_type"}
        if set(query)!=keys or query["station"]!=["LAX","DAG"] or query["report_type"]!=["3","4"] or query["tz"]!=["Etc/UTC"] or query["format"]!=["onlycomma"]:
            raise ValueError("Only the bounded KLAX/KDAG observation query is permitted")
        fields={"tmpf","dwpf","drct","sknt","alti","mslp","vsby","skyc1","skyl1","skyc2","skyl2","skyc3","skyl3","metar"}
        if set(query["data"])!=fields or len(query["data"])!=len(fields) or any(query[k]!=[v] for k,v in {"latlon":"no","elev":"no","missing":"empty","trace":"empty","direct":"no"}.items()):
            raise ValueError("Observation fields/options differ")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirects are denied; source URL must be explicit")


class PublicClient:
    def __init__(self, archive: Path, *, max_requests=128, max_bytes=128*1024*1024, deadline_seconds=900):
        self.archive = Path(archive)
        self.max_requests, self.max_bytes = int(max_requests), int(max_bytes)
        self.deadline = time.monotonic()+float(deadline_seconds)
        self.requests, self.bytes = 0, 0
        self.receipts = []
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def fetch(self, url: str, *, maximum_bytes: int, headers=None) -> dict:
        validate_url(url)
        if self.requests >= self.max_requests or time.monotonic() >= self.deadline:
            raise ValueError("Public capture request/time budget exhausted")
        supplied = dict(headers or {})
        if any(k.lower() != "range" for k in supplied):
            raise ValueError("Only a byte Range request header is permitted")
        if supplied and not re.fullmatch(r"bytes=\d+-\d*", str(next(iter(supplied.values())))):
            raise ValueError("Malformed byte Range")
        maximum_bytes = min(int(maximum_bytes),self.max_bytes-self.bytes)
        if maximum_bytes <= 0:
            raise ValueError("Public capture byte budget exhausted")
        self.requests += 1
        request = Request(url, headers={"User-Agent":"KLAX-V10-ReadOnly-Research/1.0", **supplied}, method="GET")
        started = datetime.now(timezone.utc).isoformat()
        timeout = min(20.0, max(.01, self.deadline-time.monotonic()))
        with self._opener.open(request, timeout=timeout) as response:
            if response.geturl() != url or response.status not in (200,206):
                raise ValueError("Source response status or URL differs")
            body = bytearray()
            while True:
                block = response.read(min(65536,maximum_bytes+1-len(body)))
                if not block:
                    break
                body.extend(block)
                self.bytes += len(block)
                if len(body)>maximum_bytes or self.bytes>self.max_bytes:
                    raise ValueError("Public capture response exceeded the byte budget")
                if time.monotonic() >= self.deadline:
                    raise ValueError("Public capture time budget exhausted")
            response_headers = {str(k).lower():str(v) for k,v in response.headers.items()
                if str(k).lower() in ("date","last-modified","content-type","content-range","content-length","etag")}
            status = response.status
        retrieved = datetime.now(timezone.utc).isoformat()
        raw = bytes(body)
        raw_sha = sha256(raw).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{self.requests:03d}-{stamp}-{raw_sha[:12]}.raw"
        self.archive.mkdir(parents=True, exist_ok=True)
        with (self.archive/filename).open("xb") as handle:
            handle.write(raw)
        receipt = write_immutable(self.archive/(filename+".json"), {
            "url":url, "method":"GET", "request_headers":supplied,
            "request_started_at_utc":started, "retrieved_at_utc":retrieved,
            "status_code":status, "headers":response_headers, "sha256":raw_sha,
            "bytes":len(raw), "raw_path":filename, "credentials_used":False})
        self.receipts.append(receipt)
        return {"body":raw, **receipt}

    def get_json(self, url: str) -> dict:
        value = json.loads(self.fetch(url,maximum_bytes=1024*1024)["body"])
        if not isinstance(value,dict):
            raise ValueError("Public JSON response must be an object")
        return value
