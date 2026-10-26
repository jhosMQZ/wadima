// Metadatos de las salas (deben coincidir con backend/wadima/office.py y las plantillas de áreas).

export const ROOM_ORDER = ["ops", "dev", "library", "ai", "meeting", "admin", "servers"] as const;

export const ROOM_NAMES: Record<string, string> = {
  ops: "Centro de operaciones",
  dev: "Desarrollo",
  library: "Biblioteca documental",
  ai: "Laboratorio de IA",
  meeting: "Sala de reuniones",
  admin: "Administración y control horario",
  servers: "Servidores e integraciones",
};

/** Nombre corto para las etiquetas del mapa. */
export const ROOM_SHORT: Record<string, string> = {
  ops: "Operaciones",
  dev: "Desarrollo",
  library: "Biblioteca",
  ai: "Laboratorio de IA",
  meeting: "Reuniones",
  admin: "Administración",
  servers: "Servidores",
};

export const ROOM_MODULES: Record<string, string> = {
  ops: "/ops",
  dev: "/studio",
  library: "/library",
  ai: "/agents",
  meeting: "/meetings",
  admin: "/time",
  servers: "/connections",
};

export const ROOM_ICONS: Record<string, string> = {
  ops: "◉",
  dev: "⌘",
  library: "▥",
  ai: "✦",
  meeting: "☰",
  admin: "◷",
  servers: "⇄",
};

export const ROOM_DESCRIPTIONS: Record<string, string> = {
  ops: "Panel principal: presencia de los socios, actividad reciente y escritorio de cada socio.",
  dev: "Project Studio: editor de código, Git y vista previa en vivo.",
  library: "Documentos originales, conversión a Markdown, búsqueda y memoria compartida.",
  ai: "Conexión de cada socio con Claude, tareas para agentes y Memory Agent.",
  meeting: "Sala de audio en tiempo real, actas y decisiones.",
  admin: "Entradas y salidas, aprobaciones, historial horario y auditoría.",
  servers: "Estado real de la base de datos, Google Drive, Claude, trabajos y tiempo real.",
};

export const ROOM_COLORS: Record<string, number> = {
  ops: 0x3f7cd8,
  dev: 0x8a6a4a,
  library: 0xb07a3c,
  ai: 0x8b5cf6,
  meeting: 0xc9a86a,
  admin: 0x3fa66b,
  servers: 0x4a5568,
};
