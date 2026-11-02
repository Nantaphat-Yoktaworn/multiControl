"""
MultiControl Windows Boot-Time Service Watchdog.
Runs under NT AUTHORITY\\SYSTEM in Session 0 (installed as MultiControlService).
Continuously ensures multicontrol_agent.py is running inside the active interactive
console session (Session 1, 2, etc.) with System Integrity (winsta0\\default / winlogon).

This enables remote mouse and keyboard control on:
1. Cold boot login screen (before any user logs on).
2. Lock screen / PIN entry prompt (LogonUI.exe).
3. Elevated UAC prompts.
4. Standard user desktop.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

INSTALL_DIR = Path(r"C:\ProgramData\MultiControl")
LOG_FILE = INSTALL_DIR / "watchdog.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("watchdog")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

kernel32.WTSGetActiveConsoleSessionId.argtypes = []
kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]

advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

advapi32.DuplicateTokenEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.DuplicateTokenEx.restype = wintypes.BOOL


def get_active_console_session() -> int | None:
    sid = kernel32.WTSGetActiveConsoleSessionId()
    if sid == 0xFFFFFFFF:
        return None
    return int(sid)


def find_winlogon_pid(session_id: int) -> int | None:
    TH32CS_SNAPPROCESS = 0x00000002
    h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h_snap or h_snap == wintypes.HANDLE(-1).value:
        return None

    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    target_pid = None
    try:
        if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == "winlogon.exe":
                    proc_sid = wintypes.DWORD()
                    if kernel32.ProcessIdToSessionId(pe.th32ProcessID, ctypes.byref(proc_sid)):
                        if proc_sid.value == session_id:
                            target_pid = pe.th32ProcessID
                            break
                if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(h_snap)
    return target_pid


def is_process_running(pid: int | None) -> bool:
    if not pid:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h_proc:
        return False
    try:
        exit_code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code)):
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(h_proc)


def spawn_session_agent(session_id: int, winlogon_pid: int) -> int | None:
    PROCESS_QUERY_INFORMATION = 0x0400
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, winlogon_pid)
    if not h_proc:
        logger.error("OpenProcess winlogon PID %d failed: err %d", winlogon_pid, kernel32.GetLastError())
        return None

    try:
        TOKEN_DUPLICATE = 0x0002
        TOKEN_QUERY = 0x0008
        TOKEN_ASSIGN_PRIMARY = 0x0001
        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            h_proc,
            TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY,
            ctypes.byref(h_token),
        ):
            logger.error("OpenProcessToken winlogon failed: err %d", kernel32.GetLastError())
            return None

        try:
            TokenPrimary = 1
            SecurityImpersonation = 2
            h_dup = wintypes.HANDLE()
            if not advapi32.DuplicateTokenEx(
                h_token,
                0x02000000,  # MAXIMUM_ALLOWED
                None,
                SecurityImpersonation,
                TokenPrimary,
                ctypes.byref(h_dup),
            ):
                logger.error("DuplicateTokenEx failed: err %d", kernel32.GetLastError())
                return None

            try:
                si = STARTUPINFOW()
                si.cb = ctypes.sizeof(STARTUPINFOW)
                si.lpDesktop = r"winsta0\default"

                pi = PROCESS_INFORMATION()

                pythonw_exe = INSTALL_DIR / "venv" / "Scripts" / "pythonw.exe"
                if not pythonw_exe.exists():
                    pythonw_exe = Path(sys.executable).parent / "pythonw.exe"
                    if not pythonw_exe.exists():
                        pythonw_exe = Path(sys.executable)

                launch_script = INSTALL_DIR / "launch_logged.py"
                cmd = f'"{pythonw_exe}" "{launch_script}"'

                logger.info("Spawning MultiControl session worker in Session %d: %s", session_id, cmd)
                ok = advapi32.CreateProcessAsUserW(
                    h_dup,
                    None,
                    cmd,
                    None,
                    None,
                    False,
                    0,
                    None,
                    str(INSTALL_DIR),
                    ctypes.byref(si),
                    ctypes.byref(pi),
                )
                if not ok:
                    logger.error("CreateProcessAsUserW failed: err %d", kernel32.GetLastError())
                    return None

                worker_pid = pi.dwProcessId
                kernel32.CloseHandle(pi.hProcess)
                kernel32.CloseHandle(pi.hThread)
                logger.info("Successfully spawned MultiControl worker PID %d in Session %d", worker_pid, session_id)
                return worker_pid
            finally:
                kernel32.CloseHandle(h_dup)
        finally:
            kernel32.CloseHandle(h_token)
    finally:
        kernel32.CloseHandle(h_proc)


def main() -> None:
    logger.info("MultiControl Service Watchdog started.")
    current_session: int | None = None
    worker_pid: int | None = None

    while True:
        try:
            active_sid = get_active_console_session()
            if active_sid is None:
                time.sleep(2.0)
                continue

            session_changed = (current_session is not None and active_sid != current_session)
            worker_alive = is_process_running(worker_pid)

            if session_changed and worker_pid:
                logger.info("Console session changed from %s to %d; stopping worker PID %d", current_session, active_sid, worker_pid)
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(worker_pid)], capture_output=True)
                except Exception:
                    pass
                worker_pid = None
                worker_alive = False

            if not worker_alive:
                winlogon_pid = find_winlogon_pid(active_sid)
                if winlogon_pid:
                    worker_pid = spawn_session_agent(active_sid, winlogon_pid)
                    current_session = active_sid
                else:
                    logger.debug("winlogon.exe not found yet in Session %d", active_sid)

            time.sleep(2.0)
        except Exception as exc:
            logger.error("Watchdog loop exception: %s", exc, exc_info=True)
            time.sleep(3.0)


if __name__ == "__main__":
    main()
