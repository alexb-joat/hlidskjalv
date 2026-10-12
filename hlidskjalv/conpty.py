"""Windows pseudo-consoles (ConPTY, Windows 10 1809+) through ctypes: the Windows half
of the in-page terminal. Same interface as terminal.PosixShell.

Experimental: tested on Windows CI only, never on a Windows desktop yet.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
from ctypes import wintypes

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
STARTF_USESTDHANDLES = 0x00000100
INFINITE = 0xFFFFFFFF
HPCON = wintypes.HANDLE


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HPCON)]
        k.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT
        k.ResizePseudoConsole.argtypes = [HPCON, COORD]
        k.ResizePseudoConsole.restype = ctypes.c_long
        k.ClosePseudoConsole.argtypes = [HPCON]
        k.ClosePseudoConsole.restype = None
        k.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
        k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                     wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
                                     ctypes.POINTER(PROCESS_INFORMATION)]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _k32 = k
    return _k32


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        return hasattr(_kernel32(), "CreatePseudoConsole")
    except (OSError, AttributeError):
        return False


def default_shell() -> list[str]:
    ps = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    return [ps, "-NoLogo"] if ps else [os.environ.get("COMSPEC") or "cmd.exe"]


def _coord(size) -> COORD:
    try:
        cols, rows = (max(2, min(int(v), 1000)) for v in size)
    except (TypeError, ValueError):
        cols, rows = 80, 24
    return COORD(cols, rows)


def _check(ok, what: str):
    if not ok:
        raise OSError(ctypes.get_last_error(), f"{what} failed")


class ConPtyShell:
    def __init__(self, argv: list[str], cwd: str, env: dict, size):
        import msvcrt
        k = _kernel32()
        in_r, in_w, out_r, out_w = (wintypes.HANDLE() for _ in range(4))
        _check(k.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0), "CreatePipe")
        _check(k.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0), "CreatePipe")
        self.hpc = HPCON()
        hr = k.CreatePseudoConsole(_coord(size), in_r, out_w, 0, ctypes.byref(self.hpc))
        k.CloseHandle(in_r), k.CloseHandle(out_w)  # the pseudo-console holds its own copies
        if hr != 0:
            k.CloseHandle(in_w), k.CloseHandle(out_r)
            raise OSError(hr & 0xFFFFFFFF, "CreatePseudoConsole failed")

        n = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(n))
        self._attrs = ctypes.create_string_buffer(n.value)
        _check(k.InitializeProcThreadAttributeList(self._attrs, 1, 0, ctypes.byref(n)), "InitializeProcThreadAttributeList")
        _check(k.UpdateProcThreadAttribute(self._attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self.hpc,
                                           ctypes.sizeof(HPCON), None, None), "UpdateProcThreadAttribute")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES  # null std handles: the child must use the pseudo-console only
        si.lpAttributeList = ctypes.cast(self._attrs, ctypes.c_void_p)
        block = "".join(f"{k_}={v}\0" for k_, v in env.items()) + "\0"
        env_buf = ctypes.create_unicode_buffer(block, len(block))
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        self.pi = PROCESS_INFORMATION()
        ok = k.CreateProcessW(None, cmdline, None, None, False, EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                              env_buf, cwd, ctypes.byref(si), ctypes.byref(self.pi))
        if not ok:
            err = ctypes.get_last_error()
            k.ClosePseudoConsole(self.hpc), k.CloseHandle(in_w), k.CloseHandle(out_r)
            raise OSError(err, "CreateProcess failed")
        k.CloseHandle(self.pi.hThread)
        self.fd_in = msvcrt.open_osfhandle(in_w.value, os.O_WRONLY | os.O_BINARY)
        self.fd_out = msvcrt.open_osfhandle(out_r.value, os.O_RDONLY | os.O_BINARY)
        self._closed = threading.Event()
        self._code = None
        # The output pipe only ends when the pseudo-console closes, not when the shell exits.
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        k = _kernel32()
        k.WaitForSingleObject(self.pi.hProcess, INFINITE)
        code = wintypes.DWORD()
        k.GetExitCodeProcess(self.pi.hProcess, ctypes.byref(code))
        self._code = code.value
        self._close_console()

    def _close_console(self):
        if not self._closed.is_set():
            self._closed.set()
            _kernel32().ClosePseudoConsole(self.hpc)

    def read(self) -> bytes:
        return os.read(self.fd_out, 65536)

    def write(self, data: bytes) -> None:
        os.write(self.fd_in, data)

    def resize(self, size) -> None:
        if not self._closed.is_set():
            _kernel32().ResizePseudoConsole(self.hpc, _coord(size))

    def wait(self) -> int:
        _kernel32().WaitForSingleObject(self.pi.hProcess, INFINITE)
        while self._code is None and not self._closed.wait(0.05):
            pass
        return self._code if self._code is not None else 0

    def stop(self) -> None:
        k = _kernel32()
        if k.WaitForSingleObject(self.pi.hProcess, 0) != 0:  # still running
            k.TerminateProcess(self.pi.hProcess, 1)
        self._close_console()

    def close(self) -> None:
        for fd in (self.fd_in, self.fd_out):
            try:
                os.close(fd)
            except OSError:
                pass
        _kernel32().DeleteProcThreadAttributeList(self._attrs)
        _kernel32().CloseHandle(self.pi.hProcess)
