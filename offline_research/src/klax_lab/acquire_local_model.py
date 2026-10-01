"""Acquire a pinned portable gpt-oss runtime; no inference, service or installer.

Only official upstream release/model endpoints and their allowlisted CDNs are
accepted. Published hashes are checked before extraction or executable inspection.
The GGUF is the ggml-org conversion of OpenAI's open-weight gpt-oss-20b model.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
from urllib.parse import urljoin, urlparse
import zipfile

import requests

RELEASE = "b11146"
RELEASE_COMMIT = "7fe450e19305b828c199d602c23a8337aaa1f03b"
MODEL_REPO = "ggml-org/gpt-oss-20b-GGUF"
MODEL_COMMIT = "ef9b12f2ff56c69cf32153a02784e7a3c88bf524"
MODEL_NAME = "gpt-oss-20b-MXFP4.gguf"
MAX_TRANSFER_BYTES = 18_000_000_000
ALLOWED_HOSTS = frozenset({
    "api.github.com", "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "raw.githubusercontent.com",
    "huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co",
    "cdn-lfs-eu-1.huggingface.co", "cas-bridge.xethub.hf.co",
    "us.aws.cdn.hf.co", "eu.aws.cdn.hf.co",
})


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    size: int
    sha256: str


RUNTIME_ARTIFACTS = (
    Artifact(
        "llama-b11146-bin-win-cuda-12.4-x64.zip",
        "https://github.com/ggml-org/llama.cpp/releases/download/b11146/llama-b11146-bin-win-cuda-12.4-x64.zip",
        253869799,
        "3c806a6ceccc3dae1c743ceb1a1fb2cce5b76f40bfbd4c6b7b8afb6ef45a5807",
    ),
    Artifact(
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "https://github.com/ggml-org/llama.cpp/releases/download/b11146/cudart-llama-bin-win-cuda-12.4-x64.zip",
        391443627,
        "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    ),
)
MODEL = Artifact(
    MODEL_NAME,
    f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{MODEL_NAME}",
    12109566624,
    "27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901",
)


def checked_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError(f"download URL host or scheme is not allowed: {parsed.hostname}")
    return url


def response_for(session: requests.Session, url: str, *, headers: dict | None = None):
    """Follow only explicitly allowlisted HTTPS redirects; never print signed URLs."""
    current = checked_url(url)
    hosts = []
    for _ in range(6):
        host = urlparse(current).hostname
        hosts.append(host)
        response = session.get(current, headers=headers or {}, stream=True, allow_redirects=False, timeout=(20, 120))
        if response.status_code in (301, 302, 303, 307, 308):
            destination = response.headers.get("Location")
            response.close()
            if not destination:
                raise ValueError("redirect response lacks a Location")
            current = checked_url(urljoin(current, destination))
            continue
        return response, hosts
    raise ValueError("too many official-source redirects")


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadBudget:
    """Persistent transfer accounting, updated before each bounded download chunk."""

    def __init__(self, root: Path):
        self.path = root / "data/models/provenance/transfer_usage.json"
        if self.path.exists():
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            self.transferred = int(previous["accounted_transfer_bytes"])
        else:
            self.transferred = 0

    def consume(self, count: int) -> None:
        if count < 0 or self.transferred + count > MAX_TRANSFER_BYTES:
            raise RuntimeError("portable runtime download exceeds the total 18 GB budget")
        self.transferred += count
        atomic_json(self.path, {"accounted_transfer_bytes": self.transferred, "limit_bytes": MAX_TRANSFER_BYTES})

    def check(self, remaining: int) -> None:
        if self.transferred + remaining > MAX_TRANSFER_BYTES:
            raise RuntimeError("remaining artifact exceeds the total 18 GB transfer allowance")


def fetch_metadata(session: requests.Session, url: str, destination: Path, budget: DownloadBudget) -> dict:
    response, hosts = response_for(session, url)
    try:
        if response.status_code != 200:
            raise ValueError(f"official metadata request failed with HTTP {response.status_code}")
        chunks, received = [], 0
        for chunk in response.iter_content(64 * 1024):
            received += len(chunk)
            if received > 4_000_000:
                raise ValueError("metadata response exceeds 4 MB limit")
            budget.consume(len(chunk))
            chunks.append(chunk)
        data = json.loads(b"".join(chunks))
        atomic_json(destination, {"source_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "redirect_hosts": hosts, "payload": data})
        return data
    finally:
        response.close()


def verify_upstream_metadata(root: Path, session: requests.Session, budget: DownloadBudget) -> dict:
    release_url = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{RELEASE}"
    release = fetch_metadata(session, release_url, root / f"external/llama_cpp/provenance/{RELEASE}.json", budget)
    if release["tag_name"] != RELEASE or release["target_commitish"] != RELEASE_COMMIT:
        raise ValueError("official runtime release identity changed")
    assets = {item["name"]: item for item in release["assets"]}
    for artifact in RUNTIME_ARTIFACTS:
        item = assets[artifact.name]
        if (item["size"], item.get("digest"), item["browser_download_url"]) != (artifact.size, "sha256:" + artifact.sha256, artifact.url):
            raise ValueError("published release size, SHA-256 or URL differs from pinned plan")
    model_url = f"https://huggingface.co/api/models/{MODEL_REPO}/revision/{MODEL_COMMIT}?blobs=true"
    model = fetch_metadata(session, model_url, root / "data/models/provenance/huggingface-model.json", budget)
    item = next(item for item in model["siblings"] if item["rfilename"] == MODEL_NAME)
    if model["sha"] != MODEL_COMMIT or item["size"] != MODEL.size or item["lfs"]["sha256"] != MODEL.sha256 or item["lfs"]["size"] != MODEL.size:
        raise ValueError("published model revision, byte size or LFS SHA-256 differs from pinned plan")
    plan = {"runtime_release": RELEASE, "runtime_commit": RELEASE_COMMIT, "model_repo": MODEL_REPO, "model_commit": MODEL_COMMIT, "artifacts": [artifact.__dict__ for artifact in (*RUNTIME_ARTIFACTS, MODEL)], "planned_artifact_bytes": sum(artifact.size for artifact in (*RUNTIME_ARTIFACTS, MODEL)), "transfer_cap_bytes": MAX_TRANSFER_BYTES, "model_license_metadata": model.get("cardData", {}).get("license"), "status": "UPSTREAM_METADATA_VERIFIED"}
    atomic_json(root / "data/models/provenance/pinned-plan.json", plan)
    return plan


def download_verified(session: requests.Session, artifact: Artifact, destination: Path, budget: DownloadBudget) -> dict:
    """Resume a single pinned object; verify exact length and hash before rename."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != artifact.size or hash_file(destination) != artifact.sha256:
            raise ValueError(f"cached artifact failed pinned integrity checks: {artifact.name}")
        return {"name": artifact.name, "path": str(destination), "bytes": artifact.size, "sha256": artifact.sha256, "cached": True}
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > artifact.size:
        raise ValueError("partial artifact exceeds the pinned expected size")
    budget.check(artifact.size - offset)
    if offset < artifact.size:
        headers = {"Accept-Encoding": "identity", "Range": f"bytes={offset}-{artifact.size - 1}"}
        response, hosts = response_for(session, artifact.url, headers=headers)
        try:
            if response.status_code == 206:
                expected = f"bytes {offset}-{artifact.size - 1}/{artifact.size}"
                if response.headers.get("Content-Range") != expected:
                    raise ValueError("artifact response has unexpected Content-Range")
            elif response.status_code != 200 or offset:
                raise ValueError(f"artifact download failed or refused exact resume (HTTP {response.status_code})")
            if int(response.headers.get("Content-Length", -1)) != artifact.size - offset:
                raise ValueError("artifact response size differs from pinned metadata")
            print(f"Downloading {artifact.name}: resume at {offset:,} of {artifact.size:,} bytes", flush=True)
            previous_report = offset
            with partial.open("ab") as stream:
                for chunk in response.iter_content(4 * 1024 * 1024):
                    if not chunk:
                        continue
                    if offset + len(chunk) > artifact.size:
                        raise ValueError("artifact stream exceeds pinned byte size")
                    budget.consume(len(chunk))
                    stream.write(chunk)
                    offset += len(chunk)
                    if offset - previous_report >= 512 * 1024 * 1024 or offset == artifact.size:
                        print(f"{artifact.name}: {offset:,}/{artifact.size:,} bytes", flush=True)
                        previous_report = offset
        finally:
            response.close()
    else:
        hosts = []
    if partial.stat().st_size != artifact.size or hash_file(partial) != artifact.sha256:
        raise ValueError(f"downloaded artifact failed published SHA-256 verification: {artifact.name}")
    partial.rename(destination)
    result = {"name": artifact.name, "url": artifact.url, "path": str(destination), "bytes": artifact.size, "sha256": artifact.sha256, "retrieved_at": datetime.now(timezone.utc).isoformat(), "redirect_hosts": hosts, "cached": False}
    atomic_json(destination.with_suffix(destination.suffix + ".json"), result)
    print(f"Verified SHA-256: {artifact.name}", flush=True)
    return result


def checked_zip_target(directory: Path, member: zipfile.ZipInfo) -> Path:
    """Reject traversal, absolute/drive paths, streams, symlinks and device names."""
    name = member.filename.replace("\\", "/")
    posix = PurePosixPath(name)
    if posix.is_absolute() or PureWindowsPath(name).drive or ".." in posix.parts or ":" in name:
        raise ValueError("unsafe archive member path")
    if stat.S_ISLNK(member.external_attr >> 16) or (member.external_attr & 0x400):
        raise ValueError("archive links/reparse points are forbidden")
    reserved = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
    if any(reserved.match(part) or part.endswith((" ", ".")) for part in posix.parts):
        raise ValueError("archive contains a Windows device or ambiguous path")
    target = (directory / Path(*posix.parts)).resolve()
    if not target.is_relative_to(directory.resolve()):
        raise ValueError("archive member resolves outside runtime directory")
    return target


def extract_verified_zip(artifact: Artifact, archive: Path, directory: Path) -> list[dict]:
    if archive.stat().st_size != artifact.size or hash_file(archive) != artifact.sha256:
        raise ValueError("cannot extract a ZIP without matching published integrity checks")
    files = []
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if len(members) > 10000 or sum(item.file_size for item in members) > 3_000_000_000:
            raise ValueError("archive exceeds bounded extraction size/count")
        targets = [(member, checked_zip_target(directory, member)) for member in members]
        if len({str(target).casefold() for _, target in targets}) != len(targets):
            raise ValueError("archive contains duplicate case-insensitive paths")
        for member, target in targets:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            contents = zipped.read(member)
            digest = sha256(contents).hexdigest()
            if target.exists():
                if target.stat().st_size != len(contents) or hash_file(target) != digest:
                    raise ValueError(f"existing extracted file conflicts with verified archive: {target.name}")
            else:
                partial = target.with_suffix(target.suffix + ".part")
                partial.write_bytes(contents)
                partial.rename(target)
            files.append({"path": str(target), "relative_path": str(target.relative_to(directory)), "bytes": len(contents), "sha256": digest, "archive": artifact.name, "archive_sha256": artifact.sha256})
    return files


def inspect_executables(runtime: Path, inventory: list[dict]) -> list[dict]:
    """Only --version/--help are permitted; no model, prompt or server invocation."""
    results = []
    indexed = {str(Path(item["path"]).resolve()): item for item in inventory}
    for filename, expected in indexed.items():
        path = Path(filename)
        if not path.is_relative_to(runtime.resolve()) or not path.is_file() or path.stat().st_size != expected["bytes"] or hash_file(path) != expected["sha256"]:
            raise ValueError("runtime file no longer matches the verified extraction inventory")
    for path in runtime.rglob("*"):
        if path.suffix.lower() in (".exe", ".dll") and str(path.resolve()) not in indexed:
            raise ValueError("unverified executable or DLL found beside the portable runtime")
    for name in ("llama-completion.exe", "llama-cli.exe"):
        matches = list(runtime.rglob(name))
        if not matches:
            continue
        if len(matches) != 1:
            raise ValueError("ambiguous runtime executable layout")
        executable = matches[0].resolve()
        if str(executable) not in indexed or hash_file(executable) != indexed[str(executable)]["sha256"]:
            raise ValueError("runtime executable does not match verified extraction inventory")
        for option in ("--version", "--help"):
            completed = subprocess.run([str(executable), option], cwd=executable.parent, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            output = runtime / "inspection" / f"{executable.stem}.{option[2:]}.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(completed.stdout, encoding="utf-8")
            results.append({"executable": str(executable), "argument": option, "exit_code": completed.returncode, "output": str(output), "output_sha256": hash_file(output)})
            print(f"Inspected {name} {option}: exit {completed.returncode}", flush=True)
    if not any(Path(item["executable"]).name == "llama-completion.exe" for item in results):
        raise ValueError("standalone llama-completion.exe was not found in the verified runtime")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--download-runtime", action="store_true")
    parser.add_argument("--download-model", action="store_true")
    parser.add_argument("--inspect-runtime", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    budget = DownloadBudget(root)
    session = requests.Session()
    session.headers.update({"User-Agent": "klax-offline-research/0.1"})
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "inference_performed": False, "server_started": False, "artifacts": []}
    try:
        result["pinned_plan"] = verify_upstream_metadata(root, session, budget)
        print(json.dumps({"planned_artifact_bytes": result["pinned_plan"]["planned_artifact_bytes"], "transfer_cap_bytes": MAX_TRANSFER_BYTES, "model_commit": MODEL_COMMIT, "runtime_release": RELEASE}), flush=True)
        runtime = root / f"external/llama_cpp/{RELEASE}-cuda12.4"
        inventory_path = runtime / "verified-inventory.json"
        if args.download_runtime:
            inventory = []
            for artifact in RUNTIME_ARTIFACTS:
                destination = root / "external/llama_cpp/downloads" / artifact.name
                result["artifacts"].append(download_verified(session, artifact, destination, budget))
                inventory.extend(extract_verified_zip(artifact, destination, runtime))
            atomic_json(inventory_path, {"runtime_release": RELEASE, "runtime_commit": RELEASE_COMMIT, "files": inventory})
        if args.inspect_runtime:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))["files"]
            result["inspection"] = inspect_executables(runtime, inventory)
            atomic_json(runtime / "inspection/inspection-manifest.json", {"results": result["inspection"]})
        if args.download_model:
            result["artifacts"].append(download_verified(session, MODEL, root / "data/models/gpt-oss-20b" / MODEL_COMMIT / MODEL_NAME, budget))
        result["status"] = "REQUESTED_ACQUISITION_COMPLETE"
    except BaseException as exc:
        result["status"] = "INTERRUPTED_OR_FAILED"
        # Avoid logging signed CDN URL exception strings.
        result["error_type"] = type(exc).__name__
        if isinstance(exc, (ValueError, RuntimeError)):
            result["error_summary"] = str(exc)
        raise RuntimeError(f"Portable acquisition failed ({type(exc).__name__}); see its sanitized status manifest") from None
    finally:
        session.close()
        result["accounted_transfer_bytes"] = budget.transferred
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_json(root / "data/models/provenance" / f"acquisition_{identifier}.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
