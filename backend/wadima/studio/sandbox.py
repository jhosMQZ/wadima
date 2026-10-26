"""Aislamiento de procesos de Project Studio.

En Windows (sin Docker/Podman disponibles) cada ejecución se crea suspendida con un token de integridad Baja
(ver integrity.py: no puede leer los datos del servidor ni escribir fuera del árbol de Studio), se asigna a un
Job Object con límite de memoria, de procesos activos, de CPU (tope duro) y "matar al cerrar", y después se
reanuda: ningún proceso hijo escapa de los límites. En POSIX se usan rlimits. El entorno se construye desde cero:
no hereda secretos del servidor (variables WADIMA_*, claves de API, tokens...).

Limitación documentada: no hay aislamiento de red equivalente a un contenedor y, en Windows, los archivos del
usuario anfitrión sin etiqueta siguen siendo legibles; en POSIX no hay aislamiento de archivos.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from wadima.config import get_settings

IS_WINDOWS = sys.platform == "win32"

# ----------------------------------------------------------------------------- Windows Job Objects
if IS_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")

    JobObjectExtendedLimitInformation = 9
    JobObjectCpuRateControlInformation = 15
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
    CREATE_SUSPENDED = 0x00000004
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


class Limits:
    def __init__(self, memory_mb: int, cpu_percent: int, max_processes: int):
        self.memory_mb = memory_mb
        self.cpu_percent = max(1, min(100, cpu_percent))
        self.max_processes = max_processes


class JobHandle:
    """Job Object de Windows que agrupa el árbol de procesos de una ejecución."""

    def __init__(self, limits: Limits):
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject falló")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        info.BasicLimitInformation.ActiveProcessLimit = limits.max_processes
        info.ProcessMemoryLimit = limits.memory_mb * 1024 * 1024
        info.JobMemoryLimit = limits.memory_mb * 1024 * 1024 * 2
        if not kernel32.SetInformationJobObject(
            self.handle, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject (límites) falló")
        cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        cpu.CpuRate = limits.cpu_percent * 100
        self.cpu_limited = bool(
            kernel32.SetInformationJobObject(
                self.handle, JobObjectCpuRateControlInformation, ctypes.byref(cpu), ctypes.sizeof(cpu)
            )
        )

    def assign(self, process_handle: int) -> None:
        if not kernel32.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process_handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject falló")

    def peak_memory_mb(self) -> float:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        ok = kernel32.QueryInformationJobObject(
            self.handle, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info), None
        )
        return round(info.PeakJobMemoryUsed / 1024 / 1024, 1) if ok else 0.0

    def terminate(self) -> None:
        if self.handle:
            kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)  # KILL_ON_JOB_CLOSE mata lo que quede vivo
            self.handle = None


# ----------------------------------------------------------------------------- entorno saneado
def tool_dirs() -> list[str]:
    dirs: list[str] = []
    for name in ("node", "npm", "pnpm", "python", "git"):
        path = shutil.which(name)
        if path:
            d = str(Path(path).parent)
            if d not in dirs:
                dirs.append(d)
    if IS_WINDOWS:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        dirs += [os.path.join(system_root, "System32"), system_root]
    else:
        dirs += ["/usr/bin", "/bin"]
    return dirs


def clean_env(root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    s = get_settings()
    home = root / ".home"
    tmp = root / ".tmp"
    cache = s.studio_dir / ".cache"
    for d in (home, tmp, cache):
        d.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.pathsep.join(tool_dirs()),
        "HOME": str(home),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "TMPDIR": str(tmp),
        "npm_config_cache": str(cache / "npm"),
        "npm_config_update_notifier": "false",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
        "PNPM_HOME": str(cache / "pnpm-home"),
        "npm_config_store_dir": str(cache / "pnpm-store"),
        # Sin enlaces: las uniones (junctions) creadas con integridad Baja no son legibles por Node (readlink).
        "npm_config_node_linker": "hoisted",
        "BROWSER": "none",
        "CI": "1",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    if IS_WINDOWS:
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
            if key in os.environ:
                env[key] = os.environ[key]
        env["USERPROFILE"] = str(home)
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
        Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
        Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
    env.update(extra or {})
    return env


def _spawn_windows(argv: list[str], cwd: Path, env: dict[str, str], limits: Limits, stderr: int):
    from wadima.studio import integrity

    integrity.ensure_layout(get_settings())
    job = JobHandle(limits)
    token = integrity.low_integrity_token()
    try:
        proc = integrity.LowIntegrityPopen(
            argv,
            token=token,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr,
            creationflags=CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
    except OSError:
        job.close()
        raise
    finally:
        kernel32.CloseHandle(token)
    try:
        job.assign(int(proc._handle))  # noqa: SLF001 - handle del proceso para asignarlo al Job
    except OSError:
        proc.kill()
        job.close()
        raise
    ntdll.NtResumeProcess(wintypes.HANDLE(int(proc._handle)))  # noqa: SLF001
    return proc, job


def spawn(argv: list[str], cwd: Path, env: dict[str, str], limits: Limits) -> tuple[subprocess.Popen, JobHandle | None]:
    """Lanza el proceso dentro de los límites. Devuelve (proceso, job)."""
    if IS_WINDOWS:
        return _spawn_windows(argv, cwd, env, limits, subprocess.STDOUT)

    def limit():  # pragma: no cover - solo POSIX
        import resource

        mem = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        os.setsid()

    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=limit,
    )  # noqa: PLW1509
    return proc, None


def run_capture(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    """Ejecuta un comando corto dentro del sandbox y devuelve su salida (p. ej. Git sobre un proyecto)."""
    if not IS_WINDOWS:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    proc, job = _spawn_windows(argv, cwd, env, Limits(512, 100, 16), subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        job.terminate()
        proc.communicate()
        raise
    finally:
        job.close()
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def resolve_executable(name: str) -> str | None:
    """Ruta absoluta de un ejecutable permitido (en Windows npm/pnpm son .cmd)."""
    return shutil.which(name)
