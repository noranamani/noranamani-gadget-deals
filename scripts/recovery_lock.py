"""Kernel lock releases on crash/reboot; legacy PID markers remain fail-closed."""
import os
import uuid
from contextlib import contextmanager
from pipeline_common import PipelineError


def process_alive(pid):
    if pid <= 0:
        return True
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only; never terminate.
    if not handle:
        return ctypes.get_last_error() != 87  # Missing PID only. Access denied stays locked.
    try:
        return api.WaitForSingleObject(handle, 0) != 0
    finally:
        api.CloseHandle(handle)


@contextmanager
def recoverable_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'pipeline.lock'
    guard = (root/'pipeline.guard').open('a+b')
    locked = False
    owned = False
    try:
        if guard.tell() == 0:
            guard.write(b'0')
            guard.flush()
        guard.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise PipelineError('ALREADY_RUNNING') from None
        if marker.exists():
            try:
                text = marker.read_text(encoding='ascii').strip()
                pid = int(text) if text.isdigit() else 0
            except (OSError, UnicodeError, ValueError):
                pid = 0
            if process_alive(pid):
                raise PipelineError('ALREADY_RUNNING')
            archive = root/'abandoned-locks'
            archive.mkdir(exist_ok=True)
            os.replace(marker, archive/(uuid.uuid4().hex+'.lock'))
        try:
            with marker.open('x', encoding='ascii') as stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
            owned = True
        except FileExistsError:
            raise PipelineError('ALREADY_RUNNING') from None
        yield
    finally:
        try:
            if owned:
                marker.unlink()
        finally:
            try:
                if locked:
                    guard.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
            finally:
                guard.close()  # Never unlink the shared guard inode.
