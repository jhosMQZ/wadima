"""Salas de la oficina virtual y el módulo funcional que abre cada una (coinciden con las plantillas de áreas)."""

ROOMS: dict[str, dict[str, str]] = {
    "ops": {"name": "Centro de operaciones", "module": "/ops"},
    "dev": {"name": "Desarrollo", "module": "/studio"},
    "library": {"name": "Biblioteca documental", "module": "/library"},
    "ai": {"name": "Laboratorio de IA", "module": "/agents"},
    "meeting": {"name": "Sala de reuniones", "module": "/meetings"},
    "admin": {"name": "Administración y control horario", "module": "/time"},
    "servers": {"name": "Servidores e integraciones", "module": "/connections"},
}

ROOM_KEYS = tuple(ROOMS)
