"""Windows job object that holds a pre-connect script's process tree.

Windows only: import this module only when `os.name == "nt"`.

Constants and layouts, checked against Microsoft Learn on 2026-10-09:
- JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOBOBJECT_BASIC_LIMIT_INFORMATION,
  IO_COUNTERS field order and types:
  https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
  https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
  https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-io_counters
- JobObjectExtendedLimitInformation = 9:
  https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-setinformationjobobject
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000 (same basic-limit page).
- PROCESS_SET_QUOTA = 0x0100 and PROCESS_TERMINATE = 0x0001, the rights
  AssignProcessToJobObject needs on the process handle:
  https://learn.microsoft.com/windows/win32/procthread/process-security-and-access-rights
- A process can belong to nested jobs from Windows 8 / Server 2012:
  https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import ClassVar
from typing import Optional

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IoCounters(ctypes.Structure):
    _fields_: ClassVar[list] = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_: ClassVar[list] = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_: ClassVar[list] = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32: Optional[ctypes.WinDLL] = None


def _k32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        _kernel32 = k
    return _kernel32


def _last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def _set_limit_flags(job: int, flags: int) -> None:
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = flags
    ok = _k32().SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        raise _last_error()


class WindowsJob:
    """A kill-on-close job object containing one process and its descendants."""

    def __init__(self, handle: int) -> None:
        self._handle: Optional[int] = handle

    @classmethod
    def for_pid(cls, pid: int) -> WindowsJob:
        """Create a kill-on-close job and assign process `pid` to it. Raises OSError."""
        k = _k32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            raise _last_error()
        try:
            _set_limit_flags(job, _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            proc = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if not proc:
                raise _last_error()
            try:
                if not k.AssignProcessToJobObject(job, proc):
                    raise _last_error()
            finally:
                k.CloseHandle(proc)
        except BaseException:
            k.CloseHandle(job)
            raise
        return cls(job)

    def terminate(self, exit_code: int = 1) -> None:
        """End every process in the job at once (no graceful step)."""
        if self._handle is None:
            return
        if not _k32().TerminateJobObject(self._handle, exit_code):
            raise _last_error()

    def release(self) -> None:
        """Close the job without killing it: processes left running survive.

        If kill-on-close cannot be cleared the handle stays open (raising
        OSError), so those processes keep running until the MCP exits.
        """
        if self._handle is None:
            return
        _set_limit_flags(self._handle, 0)
        self.close()

    def close(self) -> None:
        """Close the handle; with kill-on-close still set, remaining processes end."""
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        _k32().CloseHandle(handle)
