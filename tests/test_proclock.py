"""The extraction lock must refuse a second holder, on Linux (fcntl) and Windows (msvcrt)."""
import pytest

from pipeline.proclock import lock_nonblocking


def test_second_lock_on_same_file_is_refused(tmp_path):
    path = tmp_path / "x.lock"
    with path.open("a") as first, path.open("a") as second:
        lock_nonblocking(first)
        with pytest.raises(BlockingIOError):
            lock_nonblocking(second)


def test_lock_can_be_taken_again_after_release(tmp_path):
    path = tmp_path / "x.lock"
    with path.open("a") as first:
        lock_nonblocking(first)
    with path.open("a") as second:
        lock_nonblocking(second)  # no error: the first holder let go
