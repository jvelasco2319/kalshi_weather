"""Process-level offline boundary for reviewed Python experiments.

An irreversible audit hook rejects socket activity, child processes and reads of
protected paths. This is defense in depth for reviewed code, not an OS sandbox
for hostile native code or a restriction on the surrounding Codex application's
tools. The discovery backend must also obey its explicit input restrictions.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def install_guard(protected_paths: tuple[Path, ...] = ()) -> None:
    protected = tuple(str(path.resolve()).casefold() for path in protected_paths)

    def audit(event: str, args: tuple) -> None:
        if event.startswith("socket.") or event in {
            "subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "pty.spawn",
            "os.startfile", "os.startfile/2", "os.exec"
        }:
            raise PermissionError(f"Offline experiment prohibits {event}")
        if event == "ctypes.dlopen":
            raise PermissionError("Load approved native libraries before entering the experiment guard")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            candidate = str(Path(os.fsdecode(args[0])).resolve()).casefold()
            for prefix in protected:
                if candidate == prefix or candidate.startswith(prefix + os.sep):
                    raise PermissionError("Protected evaluator input is unavailable to discovery")

    sys.addaudithook(audit)
