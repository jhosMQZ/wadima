"""Extracción basada en reglas de hechos, conceptos, procedimientos y acuerdos.

Todo lo extraído así se marca como 'inferred' (inferencia, no hecho confirmado) con una confianza
moderada; solo las decisiones aprobadas por un propietario se registran como 'confirmed'.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from wadima.library.chunking import HEADING_RE, PAGE_RE


@dataclass
class Candidate:
    kind: str  # fact | concept | procedure | agreement
    title: str
    content: str
    subject_key: str | None = None
    value_text: str | None = None
    confidence: float = 0.5
    ref: dict = field(default_factory=dict)


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9%€$ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:200]


def normalize_value(text: str) -> str:
    v = normalize_key(text)
    v = re.sub(r"(\d)[ .](?=\d{3}\b)", r"\1", v)  # 5.000 -> 5000
    return v.replace(",", ".")


KV_RE = re.compile(r"^\s*(?:[-*+]\s+)?\*{0,2}([A-Za-zÁÉÍÓÚÑáéíóúñü0-9][^:|]{1,60}?)\*{0,2}\s*:\s+(.{1,160})$")
DEF_RE = re.compile(
    r"^([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü\- ]{1,50}?)\s+(?:es|son|se define como|significa|consiste en)\s+(.{10,240}?)[.;]",
)
PROC_HEADING = re.compile(r"(procedimiento|pasos|c[oó]mo |instrucciones|proceso|checklist|protocolo)", re.I)
AGREE_HEADING = re.compile(r"(acuerdos?|decisiones|conclusiones)", re.I)
STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(.+)$")

SKIP_KEYS = {"nota", "notas", "http", "https", "ejemplo", "ver", "fuente", "fecha de impresion"}


def extract_candidates(markdown: str, max_items: int = 120) -> list[Candidate]:
    out: list[Candidate] = []
    stack: list[str] = []
    page: int | None = None
    section_lines: list[str] = []
    section_title = ""
    in_code = False

    def close_section():
        nonlocal section_lines
        if section_title and section_lines:
            steps = [m.group(1).strip() for ln in section_lines if (m := STEP_RE.match(ln))]
            if PROC_HEADING.search(section_title) and len(steps) >= 2:
                out.append(
                    Candidate(
                        "procedure",
                        section_title[:300],
                        "\n".join(f"{i}. {s}" for i, s in enumerate(steps[:30], 1)),
                        confidence=0.6,
                        ref=_ref(stack, page),
                    )
                )
            elif AGREE_HEADING.search(section_title) and steps:
                for s in steps[:30]:
                    out.append(Candidate("agreement", s[:300], s, confidence=0.55, ref=_ref(stack, page)))
        section_lines = []

    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if (m := PAGE_RE.match(line.strip())) is not None:
            page = int(m.group(1))
            continue
        if (h := HEADING_RE.match(line)) is not None:
            close_section()
            level = len(h.group(1))
            stack[:] = stack[: level - 1]
            stack.append(h.group(2).strip())
            section_title = h.group(2).strip()
            continue
        section_lines.append(line)
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 2 and cells[0] and cells[1] and not set(cells[0]) <= {"-", ":"}:
                key, value = cells
                if not _is_header_like(key, value):
                    out.append(_fact(key, value, stack, page, 0.45))
            continue
        if (kv := KV_RE.match(line)) is not None:
            key, value = kv.group(1).strip(), kv.group(2).strip()
            if normalize_key(key) not in SKIP_KEYS and len(key.split()) <= 6 and not value.startswith("//"):
                out.append(_fact(key, value, stack, page, 0.5))
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            if (d := DEF_RE.match(sentence)) is not None and len(d.group(1).split()) <= 5:
                subject = d.group(1).strip()
                out.append(
                    Candidate(
                        "concept",
                        subject[:300],
                        sentence.strip(),
                        subject_key=f"def:{normalize_key(subject)}",
                        confidence=0.45,
                        ref=_ref(stack, page),
                    )
                )
        if len(out) >= max_items:
            break
    close_section()
    return out[:max_items]


def _is_header_like(key: str, value: str) -> bool:
    return key.lower() in ("campo", "clave", "concepto", "indicador", "partida", "fase") and value.lower() in (
        "valor",
        "dato",
        "importe",
        "fecha",
        "descripción",
        "descripcion",
    )


def _ref(stack: list[str], page: int | None) -> dict:
    ref: dict = {}
    if stack:
        ref["section"] = " › ".join(stack)
    if page:
        ref["page"] = page
    return ref


def _fact(key: str, value: str, stack: list[str], page: int | None, confidence: float) -> Candidate:
    key = key.strip("* ")
    return Candidate(
        "fact",
        f"{key}: {value}"[:300],
        f"{key}: {value}",
        subject_key=normalize_key(key),
        value_text=normalize_value(value)[:400],
        confidence=confidence,
        ref=_ref(stack, page),
    )
