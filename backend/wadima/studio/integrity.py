"""Aislamiento de archivos para Project Studio con niveles de integridad de Windows (sin administrador).

- Cada ejecución se crea con un token derivado del servidor, sin privilegios y con integridad Baja. Por la
  política «no write up» de Windows solo puede escribir donde la etiqueta es Baja: el árbol de proyectos de Studio.
- Los datos del servidor (directorio de datos con blobs, secretos y logs; `.env`; `~/.wadima` con PostgreSQL) se
  etiquetan con integridad Media y «no read up»: un proceso de integridad Baja no puede leerlos ni modificarlos.
- Los procesos del usuario y del servidor (integridad Media) no se ven afectados por estas etiquetas.

Limitación documentada en SECURITY.md: el resto de archivos del usuario anfitrión sin etiqueta sigue siendo
legible (no modificable) desde Studio, y los proyectos de Studio no están aislados entre sí.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("wadima.studio")

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)

    TOKEN_QUERY = 0x0008
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_ADJUST_DEFAULT = 0x0080
    DISABLE_MAX_PRIVILEGE = 0x1
    TOKEN_INTEGRITY_LEVEL = 25
    SE_GROUP_INTEGRITY = 0x20
    SE_FILE_OBJECT = 1
    LABEL_SECURITY_INFORMATION = 0x10
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    STARTF_USESTDHANDLES = 0x100
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x20002
    LOW_INTEGRITY_SID = "S-1-16-4096"

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TokenMandatoryLabel(ctypes.Structure):
        _fields_ = [("Label", _SidAndAttributes)]

    class _StartupInfo(ctypes.Structure):
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
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _adv.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _adv.GetLengthSid.argtypes = [ctypes.c_void_p]
    _adv.GetLengthSid.restype = wintypes.DWORD
    _adv.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _adv.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _adv.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.c_void_p,
    ]
    _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    _adv.GetSecurityDescriptorSacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]


def _check(ok: object, what: str) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


# ----------------------------------------------------------------------------- token de integridad Baja
def low_integrity_token() -> int:
    """Token primario derivado del proceso actual: sin privilegios (salvo los mínimos) e integridad Baja."""
    current = wintypes.HANDLE()
    _check(
        _adv.OpenProcessToken(
            _k32.GetCurrentProcess(),
            TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT,
            ctypes.byref(current),
        ),
        "OpenProcessToken",
    )
    restricted = wintypes.HANDLE()
    sid = ctypes.c_void_p()
    try:
        _check(
            _adv.CreateRestrictedToken(
                current, DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None, ctypes.byref(restricted)
            ),
            "CreateRestrictedToken",
        )
        _check(_adv.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid)), "ConvertStringSidToSid")
        label = _TokenMandatoryLabel(_SidAndAttributes(sid.value, SE_GROUP_INTEGRITY))
        size = ctypes.sizeof(label) + _adv.GetLengthSid(sid)
        _check(_adv.SetTokenInformation(restricted, TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), size), "IntegrityLevel")
    except OSError:
        if restricted:
            _k32.CloseHandle(restricted)
        raise
    finally:
        _k32.CloseHandle(current)
        if sid:
            _k32.LocalFree(sid)
    return restricted.value


def _env_block(env: dict[str, str]) -> ctypes.Array:
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    for key, value in items:
        if "=" in key[1:] or "\0" in key or "\0" in value:
            raise ValueError(f"Variable de entorno no válida: {key!r}")
    block = "".join(f"{k}={v}\0" for k, v in items) + "\0"
    return ctypes.create_unicode_buffer(block, len(block))


class LowIntegrityPopen(subprocess.Popen):
    """subprocess.Popen que crea el proceso con el token de integridad Baja (CreateProcessAsUserW).

    Hereda únicamente los extremos de las tuberías estándar (lista explícita de handles)."""

    def __init__(self, args, *, token: int, **kwargs):
        self._wadima_token = token
        super().__init__(args, **kwargs)

    def _execute_child(  # noqa: PLR0913 - firma de subprocess.Popen en Windows
        self,
        args,
        executable,
        preexec_fn,
        close_fds,
        pass_fds,
        cwd,
        env,
        startupinfo,
        creationflags,
        shell,
        p2cread,
        p2cwrite,
        c2pread,
        c2pwrite,
        errread,
        errwrite,
        *unused,
    ):
        if shell or executable is not None or preexec_fn is not None or pass_fds:
            raise ValueError("LowIntegrityPopen no admite shell, executable, preexec_fn ni pass_fds")
        if -1 in (p2cread, c2pwrite, errwrite):
            raise ValueError("LowIntegrityPopen requiere stdin, stdout y stderr explícitos")
        cmdline = args if isinstance(args, str) else subprocess.list2cmdline(args)
        handles = list(dict.fromkeys(int(h) for h in (p2cread, c2pwrite, errwrite)))
        handle_array = (wintypes.HANDLE * len(handles))(*handles)
        size = ctypes.c_size_t()
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        _check(
            _k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)), "InitializeProcThreadAttributeList"
        )
        try:
            _check(
                _k32.UpdateProcThreadAttribute(
                    attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handle_array, ctypes.sizeof(handle_array), None, None
                ),
                "UpdateProcThreadAttribute",
            )
            si = _StartupInfoEx()
            si.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            si.StartupInfo.hStdInput = int(p2cread)
            si.StartupInfo.hStdOutput = int(c2pwrite)
            si.StartupInfo.hStdError = int(errwrite)
            si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            pi = _ProcessInformation()
            env_buf = _env_block(env) if env is not None else None
            flags = creationflags | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
            try:
                _check(
                    _adv.CreateProcessAsUserW(
                        self._wadima_token,
                        None,
                        ctypes.create_unicode_buffer(cmdline),
                        None,
                        None,
                        True,
                        flags,
                        env_buf,
                        os.fsdecode(cwd) if cwd is not None else None,
                        ctypes.byref(si),
                        ctypes.byref(pi),
                    ),
                    "CreateProcessAsUser",
                )
            finally:
                self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
        finally:
            _k32.DeleteProcThreadAttributeList(attrs)
        self._child_created = True
        self._handle = subprocess.Handle(pi.hProcess)
        self.pid = pi.dwProcessId
        _k32.CloseHandle(pi.hThread)


# ----------------------------------------------------------------------------- etiquetas de integridad
PROTECTED = "ME:NR"  # integridad Media, sin lectura ni escritura desde integridad Baja
LOW_WRITABLE = "LW:"  # integridad Baja: Studio puede escribir
READ_ONLY = "ME:"  # integridad Media: Studio puede leer pero no escribir

_ACE_RE = re.compile(r"\(ML;([A-Z]*);([A-Z]*);;;([A-Z0-9-]+)\)")


def get_label(path: Path) -> tuple[str, str, str]:
    """(nivel, derechos, banderas ACE) de la etiqueta de integridad; nivel '' si no tiene (= Media implícita)."""
    sd = ctypes.c_void_p()
    err = _adv.GetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION, None, None, None, None, ctypes.byref(sd)
    )
    if err:
        raise ctypes.WinError(err, f"GetNamedSecurityInfo {path}")
    text = wintypes.LPWSTR()
    try:
        _check(
            _adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                sd, 1, LABEL_SECURITY_INFORMATION, ctypes.byref(text), None
            ),
            "ConvertSecurityDescriptorToStringSecurityDescriptor",
        )
        sddl = text.value or ""
    finally:
        _k32.LocalFree(sd)
        if text:
            _k32.LocalFree(text)
    m = _ACE_RE.search(sddl)
    if not m:
        return "", "", ""
    rights = "".join(r for r in ("NR", "NW", "NX") if r in m.group(2))
    return m.group(3), rights, m.group(1)


def set_label(path: Path, spec: str, *, inherit_only: bool = False) -> None:
    """Aplica la etiqueta (heredable si es un directorio) y la propaga a lo que ya contiene.

    Una etiqueta explícita en un subdirectorio prevalece sobre la heredada del padre, también al repropagar.
    Con inherit_only la etiqueta se aplica a los hijos pero no al propio directorio, que sigue siendo legible
    (las herramientas de Node y Git consultan los atributos de cada directorio antecesor)."""
    level, _, extra = spec.partition(":")
    rights = "NW" + extra
    inherit = ("OICIIO" if inherit_only else "OICI") if path.is_dir() else ""
    sd = ctypes.c_void_p()
    _check(
        _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"S:(ML;{inherit};{rights};;;{level})", 1, ctypes.byref(sd), None
        ),
        "ConvertStringSecurityDescriptorToSecurityDescriptor",
    )
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        sacl = ctypes.c_void_p()
        _check(
            _adv.GetSecurityDescriptorSacl(sd, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)),
            "Sacl",
        )
        err = _adv.SetNamedSecurityInfoW(str(path), SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION, None, None, None, sacl)
        if err:
            raise ctypes.WinError(err, f"SetNamedSecurityInfo {path}")
    finally:
        _k32.LocalFree(sd)


def ensure_label(path: Path, spec: str, *, explicit: bool = False, inherit_only: bool = False) -> bool:
    """Idempotente: solo reescribe (y propaga) si la etiqueta actual no es la deseada. True si cambió."""
    if not path.exists():
        return False
    level, _, extra = spec.partition(":")
    current_level, rights, flags = get_label(path)
    wanted = {"NW", *([extra] if extra else [])}
    ok = current_level == level and wanted <= set(re.findall("N[RWX]", rights))
    if path.is_dir():
        if inherit_only:  # debe ser explícita y no aplicarse al propio directorio
            ok = ok and "IO" in flags and "ID" not in flags
        else:
            ok = ok and "IO" not in flags and not (explicit and "ID" in flags)
    if ok:
        return False
    set_label(path, spec, inherit_only=inherit_only)
    return True


# ----------------------------------------------------------------------------- disposición de WADIMA
_layout_lock = threading.Lock()
_layout_done: set[tuple[str, ...]] = set()


def protected_paths(settings) -> list[tuple[Path, bool]]:
    """(ruta, solo_hijos). El directorio de datos conserva su propia etiqueta para que se pueda atravesar."""
    from wadima.config import REPO_ROOT

    return [(settings.data_dir, True), (REPO_ROOT / ".env", False), (settings.home, False)]


def ensure_layout(settings) -> None:
    """Etiqueta los datos del servidor como no legibles desde Studio y el árbol de Studio como escribible.

    Se ejecuta una vez por proceso y configuración, antes de lanzar cualquier proceso de Studio."""
    if os.name != "nt":
        return
    key = (str(settings.data_dir), str(settings.studio_dir), str(settings.home))
    with _layout_lock:
        if key in _layout_done:
            return
        settings.studio_dir.mkdir(parents=True, exist_ok=True)
        nohooks = settings.studio_dir / ".nohooks"
        nohooks.mkdir(exist_ok=True)
        changed = [
            str(p) for p, children in protected_paths(settings) if ensure_label(p, PROTECTED, inherit_only=children)
        ]
        # Etiqueta explícita: el árbol de Studio no hereda la del directorio de datos.
        if ensure_label(settings.studio_dir, LOW_WRITABLE, explicit=True):
            changed.append(str(settings.studio_dir))
        # Directorio vacío de hooks de Git: legible pero no modificable desde Studio.
        if ensure_label(nohooks, READ_ONLY, explicit=True):
            changed.append(str(nohooks))
        if changed:
            log.info("Etiquetas de integridad aplicadas: %s", ", ".join(changed))
        _layout_done.add(key)
