"""Nonblocking POSIX owner exclusion, released by the kernel on process exit."""
import os
import stat


class OwnerLock:
    def __init__(self, path, *, create=False):
        if os.name != "posix":
            raise NotImplementedError("owned Docker recovery requires POSIX file locks")
        import fcntl
        flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("owner lock must be a regular file without extra hard links")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("lease owner or recovery is still active") from error
            self.identity = {"device": info.st_dev, "inode": info.st_ino}
            self._handle = os.fdopen(fd, "r+b", buffering=0)
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
