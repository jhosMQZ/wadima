# Arquitectura de WADIMA

## Vista general

```
 Navegador de cada socio (React + Phaser + CodeMirror)          Claude Code del socio (su suscripción)
   │  REST /api/*  (cookie de sesión httpOnly + X-CSRF-Token)      │  MCP Streamable HTTP /mcp (token personal)
   │  WebSocket /ws (presencia, señales, señalización WebRTC)      │
   │  ◄──── audio WebRTC directo entre navegadores (o TURN propio) ────►
   ▼                                                              ▼
 uvicorn · un solo proceso Python (backend/wadima), HTTPS opcional con certificados locales
   ├─ FastAPI :8000 ─ API, frontend compilado, WebSocket, servidor MCP (SDK oficial)
   ├─ Starlette :8001 ─ Live Preview (origen distinto; proxy HTTP/WS a Vite o estáticos)
   ├─ Tareas periódicas (asyncio + hilos)
   │    jobs (2 s) · heartbeat de agentes (60 s) · barrido de entradas a áreas (60 s)
   │    jornadas incompletas (60 s) · reconciliación de Drive (60 s) · ancla de auditoría y copia lógica (600 s)
   └─ Procesos de Studio (npm/vite/python/git): integridad Baja + Job Objects de Windows
   │
   ├─ PostgreSQL 17 :5433 (solo 127.0.0.1; la app usa el rol wadima_app, sin propiedad de las tablas)
   ├─ var/ ─ blobs por SHA-256, proyectos de Studio (repos Git), logs, clave maestra (DPAPI)
   └─ Google Drive (OAuth) ─ originales, Markdown, notas, informes, copias lógicas
      Anthropic API (API key de cada socio)
```

Un único proceso porque el equipo tiene ~8 GB de RAM y 2 núcleos: los workers van embebidos
(`WADIMA_EMBEDDED_WORKERS`) y la cola vive en PostgreSQL, así que separarlos en otro proceso solo requiere
arrancar otro consumidor de `jobs.drain()`.

## Backend (`backend/wadima`)

| Módulo | Responsabilidad |
|---|---|
| `main.py` | App FastAPI, cabeceras de seguridad (CSP en las páginas), health check, SPA, servidor de preview embebido |
| `config.py` | Configuración `WADIMA_*` (entorno y `.env`) |
| `db.py`, `models/`, `migrations/` | SQLAlchemy 2 + Alembic. Migraciones 0001 (esquema), 0002 (subidas reanudables y conflictos), 0003 (permisos del rol de aplicación) y 0004 (organización, plantillas de áreas, control horario, tokens MCP) y 0005 (IA asignada a tareas); se aplican con el rol propietario |
| `org.py`, `api/org.py` | Identidad configurable (nombre comercial, razón social, iniciales, logotipo, colores), zona horaria, políticas de jornada, plantillas de áreas y asistente inicial |
| `timeclock.py`, `api/timeclock.py` | Jornadas con hora del servidor, una abierta por persona, jornadas incompletas, salidas declaradas/fijadas/automáticas, aprobación, totales y exportación CSV/XLSX |
| `meetings.py` | Señalización WebRTC en memoria (salas, participantes, reenvío de SDP/ICE con remitente fijado por el servidor) y credenciales TURN temporales |
| `ai_tasks.py` | Tareas de proyecto asignadas a la IA de un socio: cola, estado de cada Claude, resultado como comentario y cierre de la tarea |
| `mcp_server.py` | Servidor MCP oficial (`mcp` 2.x, sin estado, respuestas JSON) con autenticación por token y herramientas con los permisos del socio |
| `tls.py` | Autoridad local con restricciones de nombre y certificado de servidor para HTTPS en la red privada |
| `security/` | Contraseñas argon2id, sesiones de servidor, CSRF, Origin, roles por área, cifrado Fernet con clave DPAPI |
| `audit.py` | Bitácora encadenada por hash; triggers que impiden UPDATE/DELETE/TRUNCATE |
| `services/visits.py` | Entradas y salidas de áreas con hora del servidor de base de datos |
| `realtime.py` | Hub WebSocket; los eventos se publican **después del commit** y solo a quien tiene acceso al área. El cliente envía un latido cada 10 s y da la conexión por perdida a los 25 s sin respuesta |
| `services/jobs.py` | Cola persistente (`FOR UPDATE SKIP LOCKED`), deduplicación, reintentos exponenciales, recuperación de trabajos huérfanos |
| `services/merge.py` | Fusión a tres bandas (`merge3`) para notas y archivos de Studio |
| `services/summary.py`, `activity.py` | Resúmenes construidos solo con eventos registrados |
| `drive/` | Cliente REST de Drive, OAuth PKCE, motor de sincronización, proveedores de contenido |
| `library/` | Validación, extracción por formato, Markdown, fragmentación, resumen extractivo, detección de inyección |
| `memory/` | Memory Agent (ingesta, duplicados, contradicciones, vigencia, consolidación) y recuperación de contexto |
| `ai/anthropic_provider.py` | SDK oficial de Anthropic con la clave explícita de cada socio |
| `studio/` | Espacios de trabajo con rutas seguras, Git (dentro del sandbox), ejecuciones aisladas (`sandbox.py`, `integrity.py`), proxy de Live Preview |
| `backup.py`, `cli.py` | Restauración desde copia lógica, reconstrucción de originales, utilidades de administración |
| `api/` | Routers REST por dominio |

### Concurrencia y conflictos

- **Notas Markdown y archivos de Studio:** cada guardado envía la versión base. Si otro socio guardó antes, el
  servidor fusiona a tres bandas; si hay solapamiento responde 409 con la versión actual y una fusión con marcas
  de conflicto, y el socio decide. Cada guardado queda como revisión: nunca hay sobrescritura silenciosa.
- **Tareas, decisiones y demás entidades:** bloqueo optimista por `version` (409 si está desactualizada).
- **Drive:** si cambian a la vez la copia local y la remota, la fila pasa a `conflict` y se resuelve a mano
  (conservar local, aceptar remoto o conservar ambas).

### Tiempo real

El WebSocket solo transporta señales (`task.changed`, `presence`, `sync.changed`, `time.recorded`…). El cliente
vuelve a pedir los datos por REST, donde se aplican los permisos, de modo que un evento nunca filtra contenido de un
área ajena. Excepción acotada: la señalización de reuniones (`meeting.*`) reenvía descripciones SDP y candidatos ICE
solo entre participantes de la misma sala.

### Control horario y robot recolector

`POST /api/time/clock-in` toma un bloqueo por persona, comprueba que no haya otra jornada abierta (índice único
parcial como segunda barrera), escribe la entrada de auditoría y usa su hora (`clock_timestamp()` del servidor de
base de datos) como hora oficial. Cada operación añade un `time_event` (solo inserción) y, tras el commit, emite
`time.recorded`. La oficina escucha ese evento y el robot lo representa (va al socio, recoge la ficha y la lleva a la
estación de Administración); si la animación falla, el registro ya está guardado. Totales: cada jornada se reparte
entre los días locales que abarca (zona de la organización, restas en UTC) y se agrega por semana ISO y mes;
«registradas» excluye las rechazadas y «en curso» no se suma.

### Reuniones de audio

Malla WebRTC (cada participante conecta con los demás; hasta 8). El recién llegado envía las ofertas, así nunca se
cruzan dos. El micrófono se añade con `replaceTrack` sobre un transceptor ya negociado, de modo que activarlo o
silenciarlo no renegocia y el permiso solo se pide al pulsar «Activar micrófono». El indicador de voz remoto usa
`getSynchronizationSources().audioLevel`. Si el WebSocket cae, el servidor saca al socio de la sala y el cliente
vuelve a entrar al reconectar; si ICE falla, quien ofreció reinicia ICE dos veces y después muestra el error.

## Datos: dónde vive cada cosa

| Información | PostgreSQL | Drive | Notas |
|---|---|---|---|
| Usuarios, roles, áreas, miembros | Sí | Copia lógica diaria (sin hash de contraseña) | |
| Sesiones, intentos de login, estados OAuth | Sí | **Nunca** | Secretos operativos |
| Tokens OAuth de Google, API keys de Anthropic | Cifrados (Fernet) | **Nunca** | Clave maestra en `var/secrets` protegida con DPAPI |
| Bitácora de auditoría | Sí (solo inserción) | Ancla horaria con el hash cabeza (`Reports/audit-anchor.json`) | Drive conserva versiones del ancla |
| Proyectos, tareas, decisiones, comentarios | Sí | Copia lógica diaria; resumen de proyecto en `Reports/` | |
| Notas Markdown | Sí (con revisiones) | `Workspace/` o `Projects/<área>/` | Cambios hechos en Drive se importan como revisión nueva |
| Documentos originales | `var/blobs` (por SHA-256, inmutables) | `Library/<área>/` | Recuperables desde Drive por `appProperties.wadimaRef` |
| Markdown convertido | Sí | `Markdown/<área>/` | |
| Fragmentos e índice FTS | Sí | No (se regeneran) | |
| Memoria compartida | Sí | Copia lógica diaria | |
| Proyectos de Studio | `var/studio/<slug>` (repositorio Git) | No | Historial en Git local |
| Ejecuciones de Claude (API o vía MCP) | Sí (privadas de su autor) | Solo si se comparten | |
| Identidad y políticas de la organización | Sí (`organizations`, logotipo incluido) | Copia lógica diaria (logotipo en base64) | |
| Jornadas y su historial | Sí (`time_entries`, `time_events`, sin borrado) | Copia lógica diaria | Las referencias a sesiones de navegador no se copian |
| Tokens MCP | Solo su SHA-256 | **Nunca** | Se muestran una vez al crearlos |
| Audio de reuniones | **Nunca** (no pasa por el servidor) | **Nunca** | Sin grabación |

La base local es reconstruible: `restore-backup` recrea la estructura desde la copia lógica y `rebuild-blobs`
recupera los originales desde Drive verificando su SHA-256 (ver `backup.py`). Los índices y fragmentos se
regeneran reprocesando los documentos.

## Pipeline documental

`upload` → validación (extensión permitida, firma, tamaño, bombas ZIP, XXE) → SHA-256 y metadatos → blob local +
subida a Drive → trabajo `document.process`: extracción (PyMuPDF con páginas, python-docx, openpyxl con hojas,
fórmulas y valores, xlrd, python-pptx con diapositivas, CSV, JSON/YAML/XML, código) → Markdown estructurado →
evaluación de la extracción (vacía, ruido, «requiere OCR») → fragmentos por encabezado con localizador
(página/hoja/diapositiva) → resumen extractivo (general, por sección, esquema y palabras clave) → índice FTS (`simple` + `unaccent`) → aviso al
Memory Agent. OCR solo si Tesseract está en el `PATH`.

## Memory Agent

Cuatro capas en `memory_items`: **documental** (referencias), **semántica** (fragmentos recuperables),
**operativa** (decisiones aprobadas, procedimientos, tareas terminadas) y **consolidada** (resúmenes por área y
proyecto). Cada recuerdo guarda fuente, versión, localizador, área, confianza, validación
(`inferred`/`unverified`/`confirmed`/`rejected`) y vigencia. Duplicados exactos se reutilizan; casi duplicados y
contradicciones se registran como relaciones para revisión humana. Una versión nueva de la fuente deja obsoletos
los recuerdos anteriores; borrar la fuente los archiva. `memory_history` guarda cada cambio.

La recuperación (`memory/retrieval.py`) combina FTS sobre fragmentos y recuerdos, filtra por las áreas del
solicitante, prioriza lo confirmado y respeta un presupuesto de tokens. El contexto se entrega a Claude entre
delimitadores como datos no confiables.

## Frontend (`frontend/src`)

React 19 + TypeScript + Vite, `react-router` y `zustand`. Páginas cargadas bajo demanda; Phaser y CodeMirror en
chunks separados.

- **Oficina** (`office/`): mapa compacto de 40×27 casillas con 7 salas alrededor del centro de operaciones
  (todas con puerta directa), colisiones, rutas A*, sprites dibujados por código, renderer Canvas. Los textos (salas,
  nombres, placas de escritorios y racks, bocadillo del robot) van en una capa HTML superpuesta con Inter, legible a
  cualquier escala y navegable con teclado. El bucle de Phaser se duerme sin movimiento; las animaciones discretas
  (respiración, luces de equipos) avanzan dos veces por segundo y se detienen con la pestaña oculta. Racks de la sala
  de servidores = estado comprobado de cada integración; puestos del laboratorio de IA = conexión de cada socio.
- **Acceso y jornada**: pantalla de acceso con reloj del servidor y «Registrar entrada»; reloj de jornada en la barra
  superior; «Control horario» (mi jornada, historial, totales, revisión, eventos verificables).
- **Identidad**: la marca se carga sin sesión (`/api/org/public`) y se aplica como variables CSS con contraste
  garantizado; el nombre aparece en el inicio de sesión, la barra, la oficina, las salas, reuniones, informes,
  exportaciones, plantillas de Studio y el título de la pestaña.
- **Asistente inicial** (`/setup`): organización, logotipo, zona horaria, usuarios, áreas, almacenamiento e IA.
- **Importación:** `POST /api/studio/projects/{id}/upload` recibe archivos con su ruta relativa (carpeta elegida o
  soltada) o un ZIP; el cliente sube por lotes de 150 archivos. Rutas validadas con `safe_path`, sin `node_modules`,
  `.git` ni enlaces, 25 MB por archivo y 300 MB por envío o ZIP descomprimido; nunca sobrescribe salvo que se pida.
  Un proyecto «vacío» adopta el tipo detectado (Vite, Node, Python o web estática) y sus perfiles de ejecución.
- **Zoom de sala:** la cámara de Phaser interpola zoom y centro (sin los límites de cámara, que recortaban el
  destino) y la capa HTML recoloca los textos con la vista de cada fotograma.
- **Menú convencional** (`components/Layout.tsx`): todas las funciones son accesibles sin el mapa.
- **Editor de notas**: autoguardado con indicador, recuperación de borradores locales y resolución de conflictos.
- **Studio**: explorador, pestañas, CodeMirror con búsqueda/autocompletado/lint, Prettier bajo demanda, consola
  en vivo, Git (historial, diff, commits con autor) e iframe de Live Preview.

## Decisiones que se apartan de la propuesta inicial

| Propuesta | Decisión | Motivo |
|---|---|---|
| Docker/Podman para aislar ejecuciones | Token de integridad Baja + etiquetas «no read up» sobre los datos del servidor, Job Objects (memoria, CPU, procesos, matar al cerrar) y entorno saneado | No hay Docker/Podman y la sesión no es administradora. Los tokens restringidos se descartaron porque impiden crear tuberías (Node/esbuild). Limitaciones en `SECURITY.md` |
| pgvector | Búsqueda de texto completo con `unaccent` y `pg_trgm` | pgvector no viene en los binarios de Windows y no hay compilador |
| Monaco | CodeMirror 6 | Menor consumo de memoria y paquete más pequeño |
| Workers separados | Embebidos en el proceso del backend | Poca RAM; la cola en PostgreSQL permite separarlos después |
| Notificaciones push de Drive | Reconciliación periódica con `changes.list` | Los canales push requieren un endpoint HTTPS público |
| Resúmenes con LLM | Resúmenes extractivos locales | Funcionan sin API key y no inventan contenido; Claude se usa solo cuando el socio lo pide |
| Usar la suscripción Pro de Claude dentro de WADIMA | Claude Code del socio se conecta a WADIMA por MCP | Las condiciones de Anthropic no permiten que una aplicación de terceros use credenciales de planes Free/Pro/Max ni ofrezca el inicio de sesión de claude.ai (ver `INTEGRATIONS.md`) |
| Servidor de medios para las reuniones | Malla WebRTC entre navegadores | Para 3 socios basta; TURN opcional si no hay ruta directa |

## Despliegue entre los tres socios

| Equipo | Qué ejecuta | Cómo se conecta |
|---|---|---|
| Equipo principal (uno de los socios) | PostgreSQL, backend (API, WebSocket, MCP, vista previa) y el frontend compilado | Escucha en 127.0.0.1 o en su IP privada/VPN (`WADIMA_BIND_HOST`) |
| Equipos de los otros socios | Solo el navegador (y, si quieren usar su suscripción, Claude Code) | `https://<ip-o-nombre-privado>:8000`; nunca `localhost` |

- **Misma red local:** `python manage.py tls <ip-privada>` y `WADIMA_BIND_HOST=<ip-privada>`; cada socio instala la
  autoridad local (`var/secrets/tls/ca.crt`). Sin HTTPS el navegador bloquea el micrófono fuera de localhost.
- **Redes distintas:** VPN privada (Tailscale u otra) y `tailscale serve` para HTTPS; no se abren puertos del router.
- **Pérdida de conexión:** los datos viven solo en el equipo principal, así que nada se sincroniza «a medias». El
  cliente detecta el corte (latido), muestra un aviso, reintenta y recarga los datos al volver. Las notas guardan un
  borrador local; una entrada o salida solo cuenta cuando el servidor la confirma (si no, se muestra el error).
- **Copias:** `python manage.py db backup` (completa) y la copia lógica diaria en Drive.
