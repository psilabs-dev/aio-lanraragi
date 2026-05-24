import ctypes
import signal
import sys


def try_load_glibc() -> ctypes.CDLL | None:
    """Return a glibc handle if available on this platform, else None (macOS, musl-based Alpine)."""
    if sys.platform != "linux":
        return None
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None

LIBC = try_load_glibc()

def set_pdeathsig() -> None:
    """Send SIGTERM to this process when its parent dies (Linux glibc only; no-op elsewhere)."""
    if LIBC is not None:
        LIBC.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG = 1
