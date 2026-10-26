// Recogida de archivos y carpetas del equipo (selector o arrastrar y soltar) y subida por lotes.

import { api } from "../api/client";

export interface PickedFile {
  file: File;
  /** Ruta relativa dentro de lo elegido (incluye la carpeta raíz si se eligió una carpeta). */
  path: string;
}

/** Carpetas que nunca se suben: dependencias, compilados y metadatos (el servidor también las descarta). */
export const SKIPPED_DIRS = new Set([
  ".git",
  "node_modules",
  "dist",
  "build",
  "__pycache__",
  ".venv",
  "venv",
  ".tmp",
  ".home",
  ".pytest_cache",
  ".vite",
  ".next",
  ".idea",
]);

export function isSkipped(path: string): boolean {
  const parts = path.split("/");
  return (
    parts.slice(0, -1).some((p) => SKIPPED_DIRS.has(p)) ||
    [".DS_Store", "Thumbs.db"].includes(parts.at(-1) ?? "")
  );
}

export function fromInput(list: FileList | null): PickedFile[] {
  return Array.from(list ?? []).map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

async function walk(entry: FileSystemEntry, prefix: string, out: PickedFile[]): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail));
    out.push({ file, path });
    return;
  }
  if (entry.isDirectory && !SKIPPED_DIRS.has(entry.name)) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries devuelve los hijos por tandas: se llama hasta que no quedan.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail));
      if (!batch.length) break;
      for (const child of batch) await walk(child, path, out);
    }
  }
}

/** Archivos y carpetas soltados sobre la página, con sus rutas relativas. */
export async function fromDrop(dt: DataTransfer): Promise<PickedFile[]> {
  const entries = Array.from(dt.items ?? [])
    .map((i) => (i.kind === "file" ? i.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => e !== null);
  if (!entries.length) return fromInput(dt.files);
  const out: PickedFile[] = [];
  for (const e of entries) await walk(e, "", out);
  return out;
}

export interface StudioUploadReport {
  written: number;
  skipped_existing_count: number;
  skipped_ignored: number;
  rejected_count: number;
  rejected: { path: string; reason: string }[];
  template?: string;
}

const BATCH_FILES = 150;
const BATCH_BYTES = 40 * 1024 * 1024;

/** Sube a Project Studio en lotes (el servidor limita el número de partes por petición). */
export async function uploadToStudio(
  projectId: string,
  files: PickedFile[],
  opts: { target?: string; overwrite?: boolean; onProgress?: (done: number, total: number) => void } = {},
): Promise<StudioUploadReport> {
  const wanted = files.filter((f) => !isSkipped(f.path));
  const total: StudioUploadReport = {
    written: 0,
    skipped_existing_count: 0,
    skipped_ignored: files.length - wanted.length,
    rejected_count: 0,
    rejected: [],
  };
  // Si todo viene de una misma carpeta, se quita ese prefijo en el cliente para que los lotes coincidan.
  const firsts = new Set(wanted.map((f) => (f.path.includes("/") ? f.path.split("/")[0] : "")));
  const strip = firsts.size === 1 && !firsts.has("") ? `${[...firsts][0]}/` : "";
  let batch: PickedFile[] = [];
  let bytes = 0;
  let done = 0;
  const flush = async () => {
    if (!batch.length) return;
    const form = new FormData();
    for (const f of batch) form.append("files", f.file, strip ? f.path.slice(strip.length) : f.path);
    form.append("target", opts.target ?? "");
    form.append("overwrite", String(Boolean(opts.overwrite)));
    form.append("strip_root", "false");
    const r = await api<StudioUploadReport>(`/api/studio/projects/${projectId}/upload`, {
      method: "POST",
      form,
    });
    total.written += r.written;
    total.skipped_existing_count += r.skipped_existing_count;
    total.skipped_ignored += r.skipped_ignored;
    total.rejected_count += r.rejected_count;
    total.rejected.push(...r.rejected);
    total.template = r.template;
    done += batch.length;
    opts.onProgress?.(done, wanted.length);
    batch = [];
    bytes = 0;
  };
  for (const f of wanted) {
    if (batch.length >= BATCH_FILES || (bytes + f.file.size > BATCH_BYTES && batch.length)) await flush();
    batch.push(f);
    bytes += f.file.size;
  }
  await flush();
  return total;
}

export async function uploadZipToStudio(
  projectId: string,
  zip: File,
  opts: { target?: string; overwrite?: boolean } = {},
) {
  const form = new FormData();
  form.append("archive", zip, zip.name);
  form.append("target", opts.target ?? "");
  form.append("overwrite", String(Boolean(opts.overwrite)));
  return api<StudioUploadReport>(`/api/studio/projects/${projectId}/upload`, { method: "POST", form });
}

export function reportText(r: StudioUploadReport): string {
  const parts = [`${r.written} archivo(s) subidos`];
  if (r.skipped_existing_count) parts.push(`${r.skipped_existing_count} ya existían (no se sustituyeron)`);
  if (r.skipped_ignored) parts.push(`${r.skipped_ignored} omitidos (dependencias o metadatos)`);
  if (r.rejected_count) parts.push(`${r.rejected_count} rechazados`);
  return parts.join(" · ");
}
