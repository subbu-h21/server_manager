"""Which program listens on which port, and which project folder it comes from (Windows only, no extra packages).

A server's start.bat usually starts it in a new console (`start "Backend" cmd /k "cd backend && ... python main.py"`)
and exits, so its process ID is lost. What's left is the program listening on the port, and its parent processes: a
venv's python.exe inside the project, node running a script from the project's node_modules, or a cmd whose command
line names the project folder. If one of those lies in a server's folder (the folder holding its start.bat), the
program listening is that server's.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
PROCESS_BASIC_INFORMATION_CLASS = 0  # NtQueryInformationProcess classes
PROCESS_COMMAND_LINE_INFORMATION = 60  # (Windows 8.1+)
PEB_PROCESS_PARAMETERS, PARAMETERS_CURRENT_DIRECTORY = 0x20, 0x38  # offsets in a 64-bit process (as psutil reads them)
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
TCP_TABLE_OWNER_PID_LISTENER = 3
AF_INET, AF_INET6 = 2, 23
ERROR_INSUFFICIENT_BUFFER = 122
ANCESTORS = 10  # how far up the parent processes to look


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved", ctypes.c_void_p * 2), ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p)]


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wt.DWORD), ("dwLocalAddr", wt.DWORD), ("dwLocalPort", wt.DWORD),
                ("dwRemoteAddr", wt.DWORD), ("dwRemotePort", wt.DWORD), ("dwOwningPid", wt.DWORD)]


class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", wt.DWORD), ("dwLocalPort", wt.DWORD),
                ("ucRemoteAddr", ctypes.c_ubyte * 16), ("dwRemoteScopeId", wt.DWORD), ("dwRemotePort", wt.DWORD),
                ("dwState", wt.DWORD), ("dwOwningPid", wt.DWORD)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wt.FILETIME)] * 4
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
kernel32.IsWow64Process.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.BOOL)]
ntdll = ctypes.WinDLL("ntdll")
ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong)]
ntdll.NtQueryInformationProcess.restype = ctypes.c_ulong
iphlpapi = ctypes.WinDLL("iphlpapi")
iphlpapi.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL, ctypes.c_ulong,
                                         ctypes.c_int, ctypes.c_ulong]
iphlpapi.GetExtendedTcpTable.restype = wt.DWORD
INVALID_HANDLE = ctypes.c_void_p(-1).value


@dataclass
class Process:
    pid: int
    parent: int
    name: str  # e.g. "python.exe"
    path: str = ""  # the full path of its program, when Windows lets us read it
    command_line: str = ""
    cwd: str = ""  # the folder it runs in
    created: int = 0  # FILETIME ticks; 0 when unknown
    looked: bool = False  # path, command line and start time read (or Windows refused)


@dataclass
class Holder:
    """Who listens on a port: a registered server (its folder), or another program."""
    pid: int
    name: str
    folder: str | None = None  # the registered server folder it comes from
    hint: str | None = None  # otherwise: the folder it seems to run from, e.g. D:\code_files\other-project
    chain: list[Process] = field(default_factory=list, repr=False)


def listeners() -> dict[int, set[int]]:
    """port -> process IDs listening on it (IPv4 and IPv6, every address)."""
    ports: dict[int, set[int]] = {}
    for family, row_type in ((AF_INET, MIB_TCPROW_OWNER_PID), (AF_INET6, MIB_TCP6ROW_OWNER_PID)):
        size = wt.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
        for _ in range(3):  # the table can grow between the two calls
            buffer = ctypes.create_string_buffer(size.value + 4096)
            size = wt.DWORD(len(buffer))
            result = iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family,
                                                  TCP_TABLE_OWNER_PID_LISTENER, 0)
            if result != ERROR_INSUFFICIENT_BUFFER:
                break
        if result != 0:
            continue
        count = wt.DWORD.from_buffer(buffer).value
        rows = (row_type * count).from_buffer(buffer, ctypes.sizeof(wt.DWORD))  # after dwNumEntries
        for row in rows:
            ports.setdefault(socket.ntohs(row.dwLocalPort & 0xFFFF), set()).add(row.dwOwningPid)
    return ports


def snapshot() -> dict[int, Process]:
    """Every running process: its ID, parent ID and program name."""
    processes: dict[int, Process] = {}
    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle in (None, INVALID_HANDLE):
        return processes
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            processes[entry.th32ProcessID] = Process(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return processes


def _read(handle, address: int, target, size: int | None = None) -> bool:
    size = ctypes.sizeof(target) if size is None else size
    done = ctypes.c_size_t(0)
    return bool(address and kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), ctypes.byref(target), size,
                                                       ctypes.byref(done)) and done.value == size)


def _cwd(handle) -> str:
    """The folder a process runs in, from its PEB: 64-bit processes only (a 32-bit one's is laid out differently)."""
    wow64 = wt.BOOL(0)
    if ctypes.sizeof(ctypes.c_void_p) != 8 or not kernel32.IsWow64Process(handle, ctypes.byref(wow64)) or wow64.value:
        return ""
    info = PROCESS_BASIC_INFORMATION()
    if ntdll.NtQueryInformationProcess(handle, PROCESS_BASIC_INFORMATION_CLASS, ctypes.byref(info),
                                       ctypes.sizeof(info), None) != 0 or not info.PebBaseAddress:
        return ""
    parameters, folder = ctypes.c_void_p(), UNICODE_STRING()
    if not (_read(handle, info.PebBaseAddress + PEB_PROCESS_PARAMETERS, parameters)
            and _read(handle, (parameters.value or 0) + PARAMETERS_CURRENT_DIRECTORY, folder)):
        return ""
    text = ctypes.create_unicode_buffer(folder.Length // 2 + 1)
    return text.value if folder.Length and _read(handle, folder.Buffer or 0, text, folder.Length) else ""


def _details(process: Process) -> Process:
    """Fill in the program's full path, command line, folder and start time (left empty where access is denied)."""
    if process.looked or process.pid in (0, 4):
        return process
    process.looked = True
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, process.pid)
    if handle:
        process.cwd = _cwd(handle)
    else:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process.pid)
    if not handle:
        return process
    try:
        buffer, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            process.path = buffer.value
        times = [wt.FILETIME() for _ in range(4)]
        if kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            process.created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        needed = ctypes.c_ulong(0)
        status = ntdll.NtQueryInformationProcess(handle, PROCESS_COMMAND_LINE_INFORMATION, None, 0,
                                                 ctypes.byref(needed))
        if status == STATUS_INFO_LENGTH_MISMATCH and needed.value:
            raw = ctypes.create_string_buffer(needed.value)
            if ntdll.NtQueryInformationProcess(handle, PROCESS_COMMAND_LINE_INFORMATION, raw, needed,
                                               ctypes.byref(needed)) == 0:
                text = UNICODE_STRING.from_buffer(raw)
                if text.Buffer and text.Length:
                    process.command_line = ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        kernel32.CloseHandle(handle)
    return process


def ancestry(pid: int, processes: dict[int, Process]) -> list[Process]:
    """The process and its parents, nearest first. Stops at a parent that started after its child: that parent has
    exited and Windows gave its ID to a newer, unrelated process."""
    chain: list[Process] = []
    seen: set[int] = set()
    while pid in processes and pid not in seen and pid not in (0, 4) and len(chain) < ANCESTORS:
        seen.add(pid)
        process = _details(processes[pid])
        if chain and chain[-1].created and process.created and process.created > chain[-1].created:
            break
        chain.append(process)
        pid = process.parent
    return chain


def leftover_window(chain: list[Process], processes: dict[int, Process]) -> Process | None:
    """The console window a server runs in that stays open after it's stopped: a `cmd /k` (start "..." cmd /k "..."
    in its start.bat) at the top of its chain, whose parent, the start.bat that opened it, has finished. Never a
    terminal someone opened themselves: its parent (VS Code, Windows Terminal) still runs. Nor Server Manager's own
    window: a server it started in the foreground has Server Manager in its chain, so the chain ends below it."""
    mine = next((i for i, p in enumerate(chain) if p.pid == os.getpid()), len(chain))
    chain = chain[:mine]
    if not chain:
        return None
    top = chain[-1]
    if top.name.lower() != "cmd.exe" or not re.search(r"(?:^|\s)/k(?:\s|$)", top.command_line, re.I):
        return None
    parent = processes.get(top.parent)
    if parent is not None and not (_details(parent).created > top.created > 0):  # a newer process with its ID
        return None
    return top


def folder_key(folder: str) -> str:
    return os.path.normpath(folder).lower().rstrip("\\") + "\\"


def folder_pattern(key: str) -> re.Pattern:
    """The folder in a path or command line: followed by a backslash, a quote, a space or the end."""
    return re.compile(re.escape(key.rstrip("\\")) + r'(?:[\\"\s]|$)')


def _texts(process: Process) -> str:
    return f"{process.path}\n{process.command_line}".lower().replace("/", "\\")


def owner(chain: list[Process], folders: dict[str, re.Pattern]) -> str | None:
    """The registered server folder (folder_key form) this program comes from: the first process up the chain that
    names one in its path or command line, else runs in one; of the folders it names, the deepest (a server folder
    can lie inside another one's)."""
    for process in chain:
        for texts in (_texts(process), process.cwd.lower().replace("/", "\\")):
            found = [key for key, pattern in folders.items() if texts and pattern.search(texts)]
            if found:
                return max(found, key=len)
    return None


# Where installed programs live (Windows, Program Files, AppData: Python, uv, nvm's node...), not projects
SYSTEM_DIRS = [os.environ.get(v, "") for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                                                "ProgramData", "NVM_HOME", "NVM_SYMLINK")]
SYSTEM_DIRS += [os.path.join(os.environ.get("USERPROFILE", ""), d) for d in ("AppData", ".vscode")]
HOME = folder_key(os.environ.get("USERPROFILE", "") or "C:\\")
SYSTEM_DIRS = [folder_key(d) for d in SYSTEM_DIRS if d and d.strip("\\")]
PATH_IN_TEXT = re.compile(r'"([A-Za-z]:\\[^"]+)"|([A-Za-z]:\\[^\s"\']+)')
INNER_DIRS = re.compile(r"\\(?:\.?venv|env|node_modules|__pycache__|scripts|dist|build)(?:\\|$).*$", re.I)


def hint(chain: list[Process]) -> str | None:
    """The folder another program seems to run from: up its chain, the first folder it runs in, or path in its command
    line or of its program, that isn't Windows', an installed program's or the home folder itself, without its venv /
    node_modules part. None when nothing points anywhere."""
    for process in chain:
        paths = [process.cwd] + [a or b for a, b in PATH_IN_TEXT.findall(process.command_line)] + [process.path]
        for path in paths:
            if not path or folder_key(path) == HOME or any(folder_key(path).startswith(d) for d in SYSTEM_DIRS):
                continue
            folder = INNER_DIRS.sub("", path.rstrip("\\"))
            if not os.path.isdir(folder):  # a file (or something gone): its folder
                folder = os.path.dirname(folder)
            if len(folder) > 3:
                return folder
    return None


class Ports:
    """One look at the PC: who listens where, and which server folder each listener comes from."""

    def __init__(self, folders: list[str]):
        self.folders = {key: folder_pattern(key) for key in sorted({folder_key(f) for f in folders})}
        self.listening = listeners()
        self.processes = snapshot()
        self._holders: dict[int, Holder] = {}

    def holder(self, pid: int) -> Holder:
        if pid not in self._holders:
            chain = ancestry(pid, self.processes)
            name = chain[0].name if chain else self.processes.get(pid, Process(pid, 0, "a program")).name
            folder = owner(chain, self.folders)
            self._holders[pid] = Holder(pid, name, folder, None if folder else hint(chain), chain)
        return self._holders[pid]

    def holders(self, port: int) -> list[Holder]:
        return [self.holder(pid) for pid in sorted(self.listening.get(port, ()))]

    def ports_of(self, folder: str) -> dict[int, list[int]]:
        """port -> process IDs of the programs from this server folder listening there."""
        key = folder_key(folder)
        found: dict[int, list[int]] = {}
        for port, pids in self.listening.items():
            for pid in sorted(pids):
                if self.holder(pid).folder == key:
                    found.setdefault(port, []).append(pid)
        return dict(sorted(found.items()))


def kill(pid: int, tree: bool = True) -> None:
    """End a program, and with `tree` the programs it started."""
    subprocess.run(["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else []), capture_output=True, text=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
