"""Fusión a tres bandas para ediciones concurrentes de texto (notas Markdown, archivos de Studio)."""

from __future__ import annotations

from dataclasses import dataclass

import merge3


@dataclass
class MergeResult:
    merged: str
    conflicts: int


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def three_way_merge(base: str, mine: str, theirs: str) -> MergeResult:
    """Fusiona `mine` (cambios del cliente) y `theirs` (versión actual del servidor) sobre `base`.

    Si hay regiones en conflicto se devuelve el texto con marcadores y el número de conflictos;
    el llamador decide si lo acepta o pide resolución manual (nunca se descarta un cambio en silencio).
    """
    if mine == theirs:
        return MergeResult(mine, 0)
    if base == theirs:
        return MergeResult(mine, 0)
    if base == mine:
        return MergeResult(theirs, 0)
    # Asegurar salto de línea final para que la última línea se compare correctamente.
    b, m, t = (s if s.endswith("\n") or not s else s + "\n" for s in (base, mine, theirs))
    m3 = merge3.Merge3(_lines(b), _lines(m), _lines(t))
    conflicts = sum(1 for region in m3.merge_regions() if region[0] == "conflict")
    merged = "".join(
        m3.merge_lines(
            name_a="mis cambios",
            name_b="versión del servidor",
            start_marker="<<<<<<<",
            mid_marker="=======",
            end_marker=">>>>>>>",
        )
    )
    return MergeResult(merged, conflicts)
