# Hoja de ruta y estado de WADIMA

Estado verificado el 2026-10-25 (sesión 3). «Hecho» = implementado y cubierto por pruebas que pasan.

| Fase | Estado | Evidencia |
|---|---|---|
| 0. Diagnóstico | Hecho | `docs/ENVIRONMENT_AUDIT.md`; PostgreSQL 17 portátil; 6 extensiones VS Code instaladas |
| 1. Base (FastAPI + React/Vite + PostgreSQL + Alembic + health) | Hecho | `GET /api/health`; migraciones 0001-0003 |
| 2. Usuarios, áreas, invitaciones, entrada/salida, presencia WS | Hecho | `test_auth`, `test_areas`, `test_realtime`; cuentas reales creadas en `wadima` |
| 3. Oficina pixel art (Phaser, A*, sprites propios, estados reales) | Hecho | `office.test.ts`, `e2e/office.spec.ts` |
| Colaboración (proyectos, tareas, notas con fusión 3 vías, decisiones, resúmenes) | Hecho | `test_projects`, `e2e/projects.spec.ts` |
| 4. Google Drive (OAuth PKCE, subida verificada MD5, reanudable, conflictos, reconciliación) | Hecho contra simulador; **conexión real pendiente de credenciales** | `test_drive` (11) |
| 5. Biblioteca y conversión (PDF, DOCX, XLSX/XLS, PPTX, CSV, JSON/YAML/XML, código) | Hecho (OCR solo si se instala Tesseract) | `test_library` (11), `e2e/library-memory.spec.ts` |
| 6. Memory Agent (4 capas, duplicados, contradicciones, vigencia, historial, consulta con referencias) | Hecho | `test_memory` (9) |
| 7. Claude (API key por socio, verificación con /v1/models, ejecuciones privadas, count_tokens) | Hecho contra simulador; **conexión real pendiente de API keys** | `test_ai` (7) |
| 8. Project Studio (CodeMirror, Git, Live Preview Vite real, sandbox, consola restringida) | Hecho | `test_studio` (8, incluida la prueba real con Vite), `e2e/studio.spec.ts` |
| 9. Integración final | Hecho salvo bloqueos externos | `test_recovery` (4), revisión de seguridad, `e2e/responsive.spec.ts`, documentación completa |

## Sesión 2 (2026-10-25)

- Contexto recuperado de la transcripción de la sesión 1 y del repositorio. Las cuentas `socio1-3` ya existían
  en la base real (03:18) y `socio1` ya había cambiado su contraseña.
- **Studio aislado de los datos del servidor:** el código ejecutado corría con el mismo usuario que el servidor y
  podía leer `.env` y la clave maestra (y con ella las API keys de los socios) y modificar el código de WADIMA;
  además Git se ejecutaba fuera del sandbox sobre un `.git/config` escribible por ese código (comprobado: un
  `core.fsmonitor` malicioso escribía fuera del proyecto). Corregido con integridad Baja + etiquetas «no read up»
  (`studio/integrity.py`) y Git dentro del sandbox. Los tokens restringidos se descartaron (impiden crear
  tuberías con nombre, lo que rompe Node/esbuild); pnpm usa `node-linker=hoisted` porque las uniones creadas con
  integridad Baja no son legibles por Node.
- **Rol de base de datos sin propiedad** (`wadima_app`, migración 0003): la aplicación ya no puede desactivar los
  triggers de inmutabilidad. Aplicado a la base real (copia previa en `var/backups/`, `.env` anterior en
  `var/secrets/env-backup-*`).
- Memoria: reabrir una tarea terminada (también a «bloqueada») deja obsoleto su recuerdo. La oficina expone a cada
  socio solo su propio número de avisos sin leer.
- Interfaz: menú plegable y sin desbordes en móvil, tablas desplazables, estados traducidos, ligadura «fi» de la
  fuente pixel desactivada.
- Pruebas: el `conftest` creaba dos directorios temporales por ejecución y no los borraba (77 acumulados, eliminados).
- Documentación: `README.md`, `docs/ARCHITECTURE.md`, `docs/INTEGRATIONS.md`, `docs/SECURITY.md`.

## Sesión 3 (2026-10-25) — identidad, oficina compacta, control horario, reuniones e IA

| Bloque | Estado | Evidencia |
|---|---|---|
| Identidad configurable (nombre comercial, razón social, iniciales, logotipo, colores, zona horaria) | Hecho | `test_org` (11), `e2e/identity.spec.ts` |
| Áreas predefinidas, habilitar/deshabilitar, asistente inicial | Hecho | `test_org`, `e2e/identity.spec.ts` |
| Oficina compacta de 7 salas, textos en capa HTML, panel del socio, equipos con estado real | Hecho | `office.test.ts`, `e2e/office.spec.ts` (incluye consumo de CPU en reposo) |
| Control horario (hora del servidor, duplicados, incompletas, declaradas, aprobación, totales, CSV/XLSX) | Hecho | `test_timeclock` (13), `e2e/timeclock.spec.ts` |
| Robot recolector ligado a eventos reales | Hecho | `test_timeclock::test_robot_event_only_after_a_persisted_record`, `e2e/timeclock.spec.ts` |
| Reuniones con audio WebRTC (malla, micrófono bajo demanda, dispositivos, volumen, reconexión) | Hecho | `test_meetings` (5), `e2e/meetings.spec.ts` (audio real entre dos navegadores) |
| Conectividad: HTTPS local con autoridad restringida, despliegue documentado | Hecho; **no probado desde otro equipo** | `test_tls` (2) |
| Claude: investigación de opciones oficiales, MCP para la suscripción, cola de tareas, API key | Hecho; **sin cuenta real conectada** | `test_mcp` (4, cliente oficial del SDK), `e2e/ai.spec.ts` |
| Copias lógicas con organización y control horario | Hecho | `test_recovery` |
| Importar proyectos propios en Studio (carpeta, archivos o ZIP; detección Vite/Node/Python/estática) y carpetas de documentos en la Biblioteca | Hecho | `test_studio_import` (4), `e2e/import.spec.ts` |
| Zoom de la oficina a la sala a la que va el socio (desactivable) | Hecho | `e2e/import.spec.ts` |
| Tareas de proyecto asignables a personas o a la IA de un socio; un Claude por socio en el Laboratorio de IA | Hecho; base real migrada a 0005 (copia `var/backups/wadima-20261025-191226.dump`) | `test_mcp::test_project_task_assigned_to_a_partners_claude`, `e2e/ai.spec.ts` |
| Base real migrada a 0004 (copia previa `var/backups/wadima-20261025-144208.dump`) | Hecho | `verify-audit` correcto; permisos de `wadima_app` comprobados |

Decisiones: ver «Decisiones que se apartan de la propuesta inicial» y «Despliegue entre los tres socios» en
`ARCHITECTURE.md`; opciones de Claude en `INTEGRATIONS.md`.

## Resultados de la última ejecución (sesión 3)

`python manage.py test --e2e`: «Todas las comprobaciones pasaron».

- Backend: `ruff check` limpio; 120 pruebas pytest contra PostgreSQL real con la aplicación como `wadima_app`
  (incluida la prueba real con Vite y la de MCP por HTTP con el cliente oficial).
- Frontend: 26 pruebas Vitest; `tsc`, ESLint y Prettier limpios; build de producción.
- E2E: 26 pruebas Playwright (oficina y zoom de sala, control horario y robot, reuniones con audio real,
  identidad, asistente, configuración de IA/MCP, importación de carpetas en Studio y Biblioteca, proyectos, Studio,
  revisión visual de 19 vistas en escritorio y móvil).

## Pendiente

1. **Credenciales externas:** cliente OAuth de Google (`WADIMA_GOOGLE_CLIENT_ID/SECRET`); cada socio conecta su
   Claude (Claude Code + token MCP, o API key propia). Ninguna integración externa está conectada todavía.
2. **Acceso desde otros equipos:** `python manage.py tls <ip>` + `WADIMA_BIND_HOST`, o Tailscale (requiere
   administrador). Sin probar desde otro equipo; el micrófono exige HTTPS fuera de localhost.
3. **TURN** solo si se quieren reuniones entre redes distintas sin VPN.
4. `socio1` completará el asistente inicial en su próximo acceso; `socio2` y `socio3` deben cambiar su contraseña
   temporal (después, borrar `var/secrets/initial-passwords.txt`).
5. Mejoras: cuenta de Windows dedicada (o Docker/WSL) para aislar Studio también de la red y de los archivos del
   usuario; pgvector; Google Picker; OCR (Tesseract); conectar Claude Desktop sin pasar por Claude Code.
