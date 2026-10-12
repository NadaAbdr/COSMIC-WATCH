"""Cross-platform "only one extraction at a time" lock.

Linux/macOS (Docker, Airflow, CI) use fcntl; Windows uses msvcrt. Either way, a second
process trying to lock the same file gets a BlockingIOError, so callers handle one error type.
"""

try:  # Linux / macOS
    import fcntl

    def lock_nonblocking(file) -> None:
        """Lock an open file for the life of the process, or raise BlockingIOError if taken."""
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)

except ImportError:  # Windows has no fcntl
    import msvcrt

    def lock_nonblocking(file) -> None:
        """Lock the first byte of an open file, or raise BlockingIOError if already locked."""
        try:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
