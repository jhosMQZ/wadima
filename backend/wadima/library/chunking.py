"""Fragmentación semántica del Markdown, resúmenes extractivos, estimación de tokens y detección de
instrucciones incrustadas (los documentos son datos no confiables)."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

TARGET_CHARS = 1200
MAX_CHARS = 2400

PAGE_RE = re.compile(r"^<!-- página (\d+) -->$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
SHEET_RE = re.compile(r"^## Hoja: (.+)$")
SLIDE_RE = re.compile(r"^## Diapositiva (\d+)")

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (all |the )?(previous|above|prior)",
    r"ignora (todas )?(las )?instrucciones (anteriores|previas)",
    r"olvida (todas )?(las )?instrucciones",
    r"you are now (a|an|the) ",
    r"ahora eres (un|una|el|la) ",
    r"\bsystem prompt\b",
    r"\bprompt del sistema\b",
    r"<\|?(im_start|system)\|?>",
    r"^\s*(system|assistant)\s*:",
    r"(reveal|show|print) (your|the) (system|hidden) (prompt|instructions)",
    r"(exfiltrate|send) (the )?(api key|credentials|token)",
    r"(envía|enviar|revela) (la |las |el )?(clave|contraseña|token|credenciales)",
]
_INJ = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE | re.MULTILINE)

STOPWORDS = set(
    """a al algo algunas algunos ante antes como con contra cual cuando de del desde donde durante e el ella ellas
    ellos en entre era es esa esas ese eso esos esta estas este esto estos fue fueron ha han hasta hay la las le les lo
    los mas más me mi mis muy no nos o os otra otro para pero poco por que qué se sea ser si sí sin sobre son su sus
    también tan te tiene tienen todo todos tu tus un una uno unos y ya the of and to in is are was were be been for on
    with as by at from that this these those it its or an not but which can will would should has have had""".split()  # noqa: SIM905
)


def estimate_tokens(text: str) -> int:
    """Estimación aproximada (~4 caracteres por token). Se etiqueta siempre como estimación."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def detect_injection(text: str) -> list[str]:
    return sorted({m.group(0).strip()[:80] for m in _INJ.finditer(text)})


@dataclass
class Chunk:
    heading: str
    content: str
    locator: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def split_markdown(markdown: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    stack: list[str] = []
    page: int | None = None
    page_start: int | None = None
    sheet: str | None = None
    slide: int | None = None
    buf: list[str] = []

    def flush():
        nonlocal buf, page_start
        text = "\n".join(buf).strip()
        if text:
            loc: dict = {}
            if page_start is not None:
                loc["page_from"] = page_start
                loc["page_to"] = page if page is not None else page_start
            if sheet:
                loc["sheet"] = sheet
            if slide:
                loc["slide"] = slide
            if stack:
                loc["section"] = " › ".join(stack)
            for part in _split_long(text):
                chunks.append(Chunk(heading=" › ".join(stack)[:400], content=part, locator=dict(loc)))
        buf = []
        page_start = page

    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code:
            m = PAGE_RE.match(line.strip())
            if m:
                page = int(m.group(1))
                if page_start is None or not "".join(buf).strip():
                    page_start = page
                continue
            h = HEADING_RE.match(line)
            if h:
                flush()
                level = len(h.group(1))
                stack[:] = stack[: level - 1]
                stack.append(h.group(2).strip())
                if (sm := SHEET_RE.match(line)) is not None:
                    sheet = sm.group(1).strip()
                if (sl := SLIDE_RE.match(line)) is not None:
                    slide = int(sl.group(1))
                continue
        buf.append(line)
        if not in_code and sum(len(x) + 1 for x in buf) >= TARGET_CHARS and not line.strip():
            flush()
    flush()
    # Ruido de extracción: fragmentos idénticos repetidos solo se indexan una vez.
    seen: set[str] = set()
    unique = []
    for ch in chunks:
        if ch.content_hash not in seen:
            seen.add(ch.content_hash)
            unique.append(ch)
    return unique


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    lines = text.splitlines()
    # Tablas largas: se cortan por filas repitiendo el encabezado.
    if len(lines) > 2 and lines[0].startswith("|") and set(lines[1].replace("|", "").strip()) <= {"-", ":", " "}:
        header, rows = lines[:2], lines[2:]
        parts, cur = [], []
        for row in rows:
            cur.append(row)
            if sum(len(x) for x in cur) > TARGET_CHARS:
                parts.append("\n".join(header + cur))
                cur = []
        if cur:
            parts.append("\n".join(header + cur))
        return parts
    parts, cur, size = [], [], 0
    for para in re.split(r"\n\s*\n", text):
        if size + len(para) > TARGET_CHARS and cur:
            parts.append("\n\n".join(cur))
            cur, size = [], 0
        while len(para) > MAX_CHARS:
            cut = para.rfind(". ", 0, MAX_CHARS)
            cut = cut + 1 if cut > TARGET_CHARS // 2 else MAX_CHARS
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        cur.append(para)
        size += len(para)
    if cur:
        parts.append("\n\n".join(cur))
    return [p for p in parts if p.strip()]


# --------------------------------------------------------------------------- resúmenes extractivos
_SENT = re.compile(r"(?<=[.!?¿¡])\s+(?=[A-ZÁÉÍÓÚÑ¿¡0-9])")


def _prose_sections(markdown: str) -> list[tuple[str, int, str]]:
    """Devuelve (título, nivel, texto en prosa) excluyendo tablas, código y marcadores."""
    sections: list[tuple[str, int, list[str]]] = [("", 0, [])]
    in_code = False
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or s.startswith("|") or s.startswith("<!--"):
            continue
        h = HEADING_RE.match(line)
        if h:
            sections.append((h.group(2).strip(), len(h.group(1)), []))
            continue
        if s:
            sections[-1][2].append(re.sub(r"^[-*+]\s+|^\d+\.\s+|[*_`>#]", "", s))
    return [(t, lvl, " ".join(lines)) for t, lvl, lines in sections]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) > 25]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-záéíóúñü0-9]{3,}", text.lower()) if w not in STOPWORDS]


def summarize(markdown: str, max_sentences: int = 3) -> dict[str, str]:
    sections = _prose_sections(markdown)
    all_text = " ".join(t for _, _, t in sections)
    freq = Counter(_words(all_text))
    top = max(freq.values()) if freq else 1

    def score(sentence: str, position: int) -> float:
        ws = _words(sentence)
        if not ws:
            return 0.0
        base = sum(freq[w] / top for w in ws) / math.sqrt(len(ws))
        return base * (1.2 if position < 3 else 1.0)

    sentences = _sentences(all_text)
    ranked = sorted(range(len(sentences)), key=lambda i: score(sentences[i], i), reverse=True)[:max_sentences]
    short = " ".join(sentences[i] for i in sorted(ranked))
    if not short:
        short = all_text[:500].strip()
    sec_lines = []
    for title, level, text in sections:
        if not title or level > 3:
            continue
        sents = _sentences(text)
        best = max(sents, key=lambda s: score(s, 1)) if sents else text[:200]
        sec_lines.append(f"- **{title}**: {best[:300]}" if best else f"- **{title}**")
    outline = "\n".join(f"{'  ' * (lvl - 1)}- {t}" for t, lvl, _ in sections if t)
    keywords = ", ".join(w for w, _ in freq.most_common(12))
    return {
        "short": short[:1200],
        "sections": "\n".join(sec_lines[:80]),
        "outline": outline[:8000],
        "keywords": keywords,
    }
