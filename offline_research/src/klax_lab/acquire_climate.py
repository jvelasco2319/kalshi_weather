"""Finite retrieval of archived NWS CLILAX reports from the IEM archive."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from .provenance import sha256_file, write_json


def acquire(root: Path, start: str, end: str) -> dict:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first >= last or last >= datetime.now(timezone.utc).date():
        raise ValueError("Supply a bounded historical interval; end is exclusive")
    folder = root / "data/raw/climate"
    path = folder / f"CLILAX_{start}_{end}.zip"
    manifest_path = root / "data/manifests" / f"climate_{start}_{end}.json"
    if path.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if sha256_file(path) != manifest["sha256"]:
            raise ValueError("Cached climate archive hash mismatch")
        return manifest
    params = {"pil": "CLILAX", "center": "KLOX", "fmt": "zip", "sdate": start + "T00:00Z", "edate": end + "T00:00Z", "limit": 9999, "order": "asc"}
    url = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?" + urlencode(params)
    with urlopen(Request(url, headers={"User-Agent": "klax-offline-research/0.1"}), timeout=60) as response:
        content = response.read(50_000_001)
    if len(content) > 50_000_000:
        raise ValueError("Climate archive exceeds 50MB transfer cap")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        if not names or len(names) >= 9999:
            raise ValueError("Empty or possibly truncated product archive")
        if sum(x.file_size for x in archive.infolist()) > 100_000_000:
            raise ValueError("Unexpected uncompressed archive size")
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    manifest = {"url": url, "path": str(path.relative_to(root)).replace("\\", "/"),
                "source": "NWS CLILAX through Iowa Environmental Mesonet archival copy",
                "start_inclusive": start, "end_exclusive": end, "product_count": len(names),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(), "status": "downloaded",
                "limitation": "Archive reception/completeness not guaranteed; reconcile actual Kalshi settlements"}
    write_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(json.dumps(acquire(Path.cwd(), args.start, args.end), indent=2))
