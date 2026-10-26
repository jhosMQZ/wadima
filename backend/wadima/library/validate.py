"""Validación de archivos subidos: extensión permitida, firma (magic bytes), tamaño y bombas ZIP."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from wadima.config import get_settings


class ValidationError(Exception):
    pass


CODE_EXTS = {
    "py": "python",
    "js": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    "jsx": "jsx",
    "java": "java",
    "cs": "csharp",
    "php": "php",
    "go": "go",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "hpp": "cpp",
    "rs": "rust",
    "rb": "ruby",
    "sh": "bash",
    "ps1": "powershell",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "kt": "kotlin",
    "swift": "swift",
    "r": "r",
    "scala": "scala",
    "lua": "lua",
    "dart": "dart",
}

TYPES: dict[str, tuple[str, str]] = {
    # ext: (familia, mime)
    "pdf": ("pdf", "application/pdf"),
    "docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "xlsm": ("xlsx", "application/vnd.ms-excel.sheet.macroEnabled.12"),
    "xls": ("xls", "application/vnd.ms-excel"),
    "pptx": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "csv": ("csv", "text/csv"),
    "tsv": ("csv", "text/tab-separated-values"),
    "txt": ("text", "text/plain"),
    "md": ("markdown", "text/markdown"),
    "markdown": ("markdown", "text/markdown"),
    "json": ("json", "application/json"),
    "yaml": ("yaml", "application/yaml"),
    "yml": ("yaml", "application/yaml"),
    "xml": ("xml", "application/xml"),
    **{ext: ("code", "text/plain") for ext in CODE_EXTS},
}


@dataclass
class FileInfo:
    filename: str
    ext: str
    family: str
    mime: str


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFC", name).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    return name[:200] or "documento"


def validate(filename: str, content: bytes) -> FileInfo:
    s = get_settings()
    name = safe_filename(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in TYPES:
        raise ValidationError(f"Tipo de archivo no admitido: .{ext or '(sin extensión)'}")
    if not content:
        raise ValidationError("El archivo está vacío")
    if len(content) > s.max_upload_mb * 1024 * 1024:
        raise ValidationError(f"El archivo supera el límite de {s.max_upload_mb} MB")
    family, mime = TYPES[ext]
    head = content[:8]
    if family == "pdf" and not content.lstrip()[:5].startswith(b"%PDF-"):
        raise ValidationError("El contenido no es un PDF válido")
    if family in ("docx", "xlsx", "pptx"):
        if not head.startswith(b"PK\x03\x04"):
            raise ValidationError("El contenido no es un documento Office (OOXML) válido")
        check_zip(content, family)
    if family == "xls" and not head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValidationError("El contenido no es un libro .xls válido")
    textual = family in ("text", "markdown", "csv", "json", "yaml", "xml", "code")
    if textual and b"\x00" in content[:8192]:
        raise ValidationError("El archivo de texto contiene datos binarios")
    return FileInfo(filename=name, ext=ext, family=family, mime=mime)


def check_zip(content: bytes, family: str) -> None:
    """Protege contra bombas ZIP y comprueba la estructura mínima del paquete OOXML."""
    s = get_settings()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValidationError("Paquete ZIP dañado") from exc
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.compress_size and info.file_size / max(info.compress_size, 1) > 200 and info.file_size > 10_000_000:
            raise ValidationError("Relación de compresión sospechosa (posible bomba ZIP)")
        if ".." in info.filename or info.filename.startswith("/"):
            raise ValidationError("Rutas no válidas dentro del paquete")
    if total > s.max_unzipped_mb * 1024 * 1024:
        raise ValidationError("El documento descomprimido es demasiado grande")
    names = set(zf.namelist())
    required = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[family]
    if required not in names:
        raise ValidationError(f"Falta {required}: no es un archivo .{family} válido")
