"""Reduce automatic crash-report exposure for this process only."""
import sys


def restrict_crash_reports():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WerSetFlags.argtypes = [wintypes.DWORD]
        kernel.WerSetFlags.restype = ctypes.c_long
        # WER_FAULT_REPORTING_FLAG_NOHEAP. This is not a manual-dump barrier.
        if kernel.WerSetFlags(1) < 0:
            raise RuntimeError("Не удалось ограничить сбор памяти в отчёты Windows")
    else:
        import resource

        _, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
