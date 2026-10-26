# WADIMA

Workspace colaborativo local-first para tres socios: oficina virtual pixel art compacta (Phaser) con robot
recolector de registros, control horario con la hora del servidor, sala de reuniones con audio en tiempo real
(WebRTC), proyectos y notas con edición concurrente, biblioteca documental con conversión a Markdown, Memory Agent,
Claude (suscripción de cada socio vía Claude Code + MCP, o API key propia), Project Studio con Live Preview y
sincronización con Google Drive. El nombre comercial, el logotipo y los colores se configuran en la aplicación.

Estado por fases y pendientes: [`docs/ROADMAP.md`](docs/ROADMAP.md). Arquitectura: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Drive y Claude: [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md). Seguridad: [`docs/SECURITY.md`](docs/SECURITY.md).

## Requisitos

| Herramienta | Versión probada | Notas |
|---|---|---|
| Windows 11 | 10.0.26300 | Sin privilegios de administrador. Linux/macOS: el sandbox usa rlimits (no probado) |
| Python + [uv](https://docs.astral.sh/uv/) | 3.12 · uv 0.11 | `uv` crea `backend/.venv` |
| Node.js + pnpm | 24 · pnpm 10 | |
| Git | 2.55 | Usado por Project Studio |
| PostgreSQL | 17 | Binarios portátiles (ver abajo) en el puerto 5433, solo 127.0.0.1 |
| Chrome | cualquiera reciente | Solo para las pruebas Playwright (`channel: "chrome"`) |

**PostgreSQL portátil (sin instalador ni administrador):** descargue el ZIP «Windows x86-64 binaries» de la versión
17 desde <https://www.enterprisedb.com/download-postgresql-binaries> y extraiga la carpeta `pgsql` en
`%USERPROFILE%\.wadima\tools\pgsql` (debe existir `...\pgsql\bin\pg_ctl.exe`). Otra ubicación: `WADIMA_PG_BIN`.
Si PostgreSQL ya está instalado en el `PATH`, `manage.py` usa ese.

## Instalación

```powershell
python manage.py setup
```

Hace, en orden: `uv sync` (backend), `pnpm install` (frontend), `db init` (crea el clúster en
`%USERPROFILE%\.wadima\pgdata`, los roles `wadima` —propietario, para migraciones— y `wadima_app` —el de la
aplicación, sin propiedad de las tablas—, las bases `wadima`, `wadima_test` y `wadima_e2e`, y escribe sus credenciales
en `.env`), `migrate` y `users` (las tres cuentas iniciales y el área «General»).

En una instalación existente basta con `python manage.py db backup`, `python manage.py migrate` y reiniciar
WADIMA (si es anterior a la separación de roles, ejecute antes `python manage.py db init`).

**Primer acceso del administrador:** si la organización no está configurada se abre el asistente inicial
(nombre de la organización, logotipo, zona horaria, usuarios, áreas predefinidas, almacenamiento e IA). Se puede
volver a ejecutar en **Configuración → Identidad de la organización**.

Las contraseñas temporales de `socio1` (administrador), `socio2` y `socio3` se muestran una vez y quedan en
`var/secrets/initial-passwords.txt`. Cada socio debe cambiarla en su primer acceso (la API queda bloqueada hasta
hacerlo). Entréguelas por un canal privado y borre el archivo cuando las tres se hayan cambiado.

## Ejecución

| Comando | Qué hace |
|---|---|
| `python manage.py dev` | PostgreSQL + backend en `:8000` + Vite (recarga en caliente del frontend) en `:5173`. Abra <http://127.0.0.1:5173> |
| `python manage.py start` | Modo producción local: compila el frontend si falta y lo sirve el backend en <http://127.0.0.1:8000> |
| `python manage.py db start\|stop\|status` | Controla PostgreSQL |
| `python manage.py db backup` | `pg_dump` de la base real en `var/backups/` |
| `python manage.py migrate` | Aplica migraciones de Alembic |
| `python manage.py build` | Compila el frontend a `frontend/dist` |
| `python manage.py tls <ip-o-nombre>` | Certificados HTTPS para la red privada (necesarios para el micrófono desde otros equipos) |

La vista previa de Project Studio se publica siempre en un origen propio (`:8001`) dentro del mismo proceso.

Comandos de administración del backend (`cd backend; uv run python -m wadima.cli <comando>`):

- `reset-password <usuario>`: nueva contraseña temporal con cambio obligatorio.
- `verify-audit`: verifica la cadena de hashes de la bitácora de auditoría.
- `restore-backup <copia.json> [--force]`: restaura una copia lógica de `Drive/WADIMA/Backups` en una base vacía.
- `rebuild-blobs`: descarga desde Drive los originales que falten en `var/blobs` (verifica SHA-256).

## Pruebas

```powershell
python manage.py test          # ruff, pytest, ESLint, tsc y Vitest
python manage.py test --e2e    # además, compila y ejecuta Playwright
```

- **Backend:** pytest contra PostgreSQL real (`wadima_test`); Drive y Anthropic se prueban contra servidores
  simulados en proceso. `-m network` ejecuta además la prueba real de Vite (instala dependencias de npm).
- **E2E:** Playwright arranca su propio backend en `:8010`/`:8011` sobre `wadima_e2e`, que se **borra y se vuelve a
  crear** en cada ejecución con datos de demostración («Ana (E2E)», «Bruno (E2E)», «Carla (E2E)»). La base real
  `wadima` nunca se toca. Las reuniones se prueban con audio real entre dos navegadores usando el micrófono
  simulado de Chrome; el servidor MCP, con peticiones reales por HTTP y con el cliente oficial del SDK de MCP.

## Estructura

```
backend/   FastAPI (wadima/), migraciones Alembic, pruebas pytest
frontend/  React + TypeScript + Vite; oficina en src/office (Phaser); pruebas e2e/ (Playwright)
docs/      Auditoría del entorno, hoja de ruta, arquitectura, integraciones y seguridad
var/       Datos de ejecución, ignorado por Git: blobs, proyectos de Studio, logs y secretos locales
manage.py  Tareas reproducibles (solo biblioteca estándar)
```

## Configuración

Copie `.env.example` a `.env` (lo hace `db init`). Las variables relevantes y su significado están comentadas en
ese archivo; ningún valor real se versiona. Las API keys de Anthropic no van en `.env`: cada socio la introduce en
**Conexiones → Claude** y se guarda cifrada.

## Uso diario

1. Inicie sesión y, en **Acceso a la oficina**, pulse **Registrar entrada** (hora del servidor; se muestra para
   verificarla). El reloj de la barra superior muestra la jornada; **Registrar salida** la cierra.
2. La oficina muestra a los socios con su estado real; cada sala abre su módulo (doble clic o «Abrir módulo») y
   también se puede usar el menú lateral. El robot de Administración recoge cada entrada o salida registrada.
3. **Control horario:** historial, totales diarios/semanales/mensuales (registradas vs. aprobadas), revisión de
   jornadas incompletas o declaradas y exportación a CSV/Excel.
4. **Reuniones:** «Unirse a la reunión» y después «Activar micrófono». No se graba nada.
5. **Configuración de IA:** cada socio conecta su suscripción de Claude desde Claude Code (token MCP) o su API key.
   En cualquier proyecto, una tarea se puede asignar a una persona y/o a la IA de un socio («Claude de …»); su
   Claude Code la recoge cuando su dueño se lo pide y entrega el resultado como comentario de la tarea. Cada
   Claude aparece en el Laboratorio de IA de la oficina con su estado (disponible, en cola, trabajando).
6. **Sus propios proyectos y documentos:** en **Desarrollo → Nuevo proyecto de código** elija «Importar una carpeta de
   mi equipo» o «Importar un archivo ZIP» (se omiten `node_modules`, `.git` y compilados; el tipo de proyecto se
   detecta solo). En un proyecto abierto, **Subir** o arrastrar archivos y carpetas sobre la lista. En la
   **Biblioteca**, «Elegir carpeta» o arrastrar una carpeta sube todos los documentos admitidos, etiquetados con
   el nombre de sus carpetas.
7. **Oficina:** al ir con su avatar a una sala, la vista se acerca a ella; «Vista completa» vuelve al mapa entero
   (se puede desactivar con la casilla «Acercar la sala al entrar en ella»).

## Acceso de los otros socios

Por defecto todo escucha solo en `127.0.0.1`. **No abra puertos del router.** Los otros socios solo necesitan un
navegador y la dirección privada del equipo principal (nunca `localhost`):

- **Misma red local:** `python manage.py tls <ip-privada>`, añada `WADIMA_BIND_HOST=<ip-privada>` a `.env`, ejecute
  `python manage.py start` y entregue a cada socio `var/secrets/tls/ca.crt`. Abren `https://<ip-privada>:8000`.
- **Redes distintas:** VPN privada (Tailscale u otra) y `docs/SECURITY.md#acceso-remoto`.

El micrófono de las reuniones exige HTTPS fuera de `localhost`. Detalles de red y de qué se ejecuta en cada equipo:
`docs/ARCHITECTURE.md#despliegue-entre-los-tres-socios`.
