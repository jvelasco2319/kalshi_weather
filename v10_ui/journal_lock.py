"""OS-owned journal lock: released automatically if a server exits or crashes."""
from contextlib import contextmanager
import sys


@contextmanager
def journal_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/"write.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise RuntimeError("Another dashboard is writing the journal. Try again shortly.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            release()
