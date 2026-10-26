# Seguridad de WADIMA

## Supuestos

- Los tres socios son usuarios autenticados de confianza para colaborar, pero sus **credenciales y ejecuciones
  privadas** (API keys de Claude, sesiones) deben quedar separadas entre ellos.
- Los **documentos** subidos y el **código** de Project Studio se tratan como no confiables.
- WADIMA corre en el equipo de un socio, sin privilegios de administrador y sin Docker. Nada escucha fuera de
  `127.0.0.1` salvo que se configure una VPN privada.

## Autenticación y sesiones

- Contraseñas con **argon2id**; mínimo 10 caracteres, tres tipos de carácter y sin contener el usuario. Las
  contraseñas iniciales son temporales: la API queda bloqueada hasta cambiarlas.
- Bloqueo tras 5 intentos fallidos en 15 minutos por usuario; cada intento queda registrado.
- Sesiones de servidor: token aleatorio de 256 bits en cookie `httpOnly`, `SameSite=Lax` (`Secure` con
  `WADIMA_COOKIE_SECURE=true` cuando se sirve por HTTPS). En la base solo se guarda su SHA-256. Caducan por
  inactividad (12 h) y de forma absoluta (7 días); cambiar la contraseña revoca las demás sesiones, y
  **Seguridad** permite ver y cerrar sesiones y consultar el historial de acceso.
- **CSRF:** las peticiones que modifican datos exigen el token de la sesión en `X-CSRF-Token` y un `Origin`
  incluido en `WADIMA_ALLOWED_ORIGINS`. El WebSocket comprueba también el `Origin`. No hay CORS habilitado.

## Autorización

- Rol global (`admin` / `member`) y rol por área (`viewer` < `editor` < `owner`); el administrador es `owner`
  implícito. Un área ajena responde 404 para no revelar su existencia.
- El WebSocket solo transporta señales; los datos se vuelven a pedir por REST, donde se aplican los permisos.
- La memoria, la búsqueda y el contexto enviado a Claude se filtran por las áreas del solicitante.
- Las ejecuciones de Claude son privadas de su autor hasta que las comparte; cada socio solo puede usar su clave.

## Validación y protección web

- Entradas validadas con Pydantic; SQL siempre parametrizado (SQLAlchemy / `text()` con parámetros).
- **XSS:** el Markdown se convierte con `marked` y se sanea con DOMPurify; CSP `script-src 'self'`,
  `object-src 'none'`, `frame-ancestors 'self'`; `X-Content-Type-Options: nosniff`.
- **SSRF:** el servidor solo contacta URLs fijas de configuración (Google, Anthropic). El proxy de Live Preview
  solo reenvía a `127.0.0.1` en el puerto que WADIMA asignó a ese proyecto. La biblioteca no descarga URLs.
- **Rutas:** rutas relativas sin `..`, unidad, `.git`, enlaces simbólicos ni uniones (`safe_path`); los blobs se
  direccionan por SHA-256 dentro de su almacén.
- **Subidas:** lista blanca de extensiones, comprobación de firma, 50 MB por archivo, límite de 300 MB
  descomprimidos (bombas ZIP) y XML con `defusedxml` (sin entidades externas).
- **Logotipo de la organización:** solo PNG, JPEG o WebP (firma comprobada, 512 KB); SVG no se admite porque
  puede contener scripts. Se sirve con `nosniff` y `Content-Security-Policy: default-src 'none'`.
- **Micrófono:** `Permissions-Policy: microphone=(self)` (cámara y geolocalización desactivadas). El permiso solo se
  pide al pulsar «Activar micrófono».
- **Inyección de instrucciones:** los fragmentos con instrucciones sospechosas se marcan; todo contenido
  recuperado se entrega a Claude delimitado como datos no confiables y el modelo no recibe herramientas.

## Secretos

| Secreto | Dónde | Protección |
|---|---|---|
| Contraseñas de los roles `wadima` (propietario) y `wadima_app` (aplicación) | `.env` | Ignorado por Git; ilegible desde Studio |
| Contraseña del superusuario de PostgreSQL | `%USERPROFILE%\.wadima\secrets` | Ilegible desde Studio |
| Clave maestra Fernet | `var/secrets/master.key.dpapi` (DPAPI del usuario) o `WADIMA_MASTER_KEY` | Ilegible desde Studio |
| Tokens OAuth de Google, API keys de Anthropic | PostgreSQL, cifrados con la clave maestra | Nunca se devuelven al navegador ni van a Drive |
| Client secret de Google | `.env` | Ignorado por Git |
| Contraseñas temporales iniciales | `var/secrets/initial-passwords.txt` | Bórrelo cuando los socios las hayan cambiado |
| Tokens MCP de los socios | PostgreSQL, solo SHA-256 | Se muestran una vez; revocables; nunca van a Drive ni a la copia lógica |
| Certificado HTTPS local | `var/secrets/tls/server.key` | La clave de la autoridad local no se guarda; la autoridad solo puede firmar los nombres indicados |
| Secreto TURN (opcional) | `.env` | Nunca sale del servidor: los clientes reciben credenciales temporales HMAC |

PostgreSQL escucha solo en `127.0.0.1:5433` con `scram-sha-256`. Las copias lógicas de Drive excluyen hashes de
contraseñas, sesiones, tokens y claves.

## Auditoría y registros horarios

- `audit_log` es una cadena SHA-256 (cada entrada incluye el hash anterior) con la hora del servidor de base de
  datos. Triggers impiden `UPDATE`, `DELETE` y `TRUNCATE`. `python -m wadima.cli verify-audit` recorre la cadena,
  y con Drive conectado el hash cabeza se ancla cada hora en `Reports/audit-anchor.json` (Drive guarda sus
  versiones).
- `area_visits`: la entrada es inmutable y la salida solo se puede fijar una vez; no se pueden borrar.
- **Control horario** (`time_entries`, `time_events`): la hora oficial es la del servidor de base de datos; el
  cliente nunca la envía para una entrada o salida normal. Un trigger impide cambiar los datos de entrada, modificar
  una salida ya fijada, aprobar una jornada abierta o borrar jornadas; `time_events` es de solo inserción (sin
  UPDATE, DELETE ni TRUNCATE). El rol `wadima_app` tampoco tiene DELETE sobre ellas. Las salidas declaradas,
  automáticas o fijadas por un aprobador quedan marcadas con su tipo, autor y motivo y en la bitácora encadenada.
  Nadie aprueba ni cierra sus propias jornadas (aprueban los administradores y los propietarios del área de
  Administración).
- La aplicación se conecta como `wadima_app`, que **no es propietario** de las tablas: no puede desactivar los
  triggers, vaciar o borrar tablas, ni actualizar o borrar `audit_log` o borrar `area_visits` (migración 0003;
  verificado por `test_app_role_cannot_bypass_immutability`). El rol propietario `wadima` solo se usa para
  migraciones y copias.
- **Limitación:** la contraseña del propietario está en el `.env` del servidor; quien controle el proceso o los
  archivos del servidor podría usarla. Para ese caso, la cadena de hashes y el ancla externa en Drive hacen
  detectable la manipulación, aunque no la impiden.

## Project Studio: ejecución aislada

Ejecutar en Studio equivale a ejecutar código de los miembros del área en el equipo anfitrión, así que se limita
en varias capas (Windows, sin administrador):

1. **Job Object** por ejecución: memoria (1024 MB), CPU (50 %, tope duro), procesos (64), tiempo (900 s) y
   «matar al cerrar»; el proceso se crea suspendido y se asigna antes de ejecutar nada. Configurable con
   `WADIMA_RUN_*`.
2. **Integridad Baja** (`studio/integrity.py`): token derivado del servidor sin privilegios y con nivel de
   integridad Bajo. Por la política de Windows *no write up* solo puede escribir en `var/studio`. Al arrancar (y
   antes de cada ejecución) WADIMA etiqueta como «Media, sin lectura desde niveles inferiores» el contenido de
   `var/` (blobs, secretos, logs, copias), el archivo `.env` y `%USERPROFILE%\.wadima` (PostgreSQL y su
   contraseña). Un proceso de Studio no puede leerlos ni modificar el código de WADIMA. Lo verifica
   `test_sandbox_isolates_server_data_and_git`.
3. **Entorno reconstruido** desde cero: sin variables `WADIMA_*`, claves ni tokens; `HOME`, `TEMP` y cachés
   dentro del proyecto.
4. **Git también se ejecuta en el sandbox:** el código del proyecto puede escribir `.git/config`, así que un
   `core.fsmonitor`, filtro o diff externo malicioso solo correría con integridad Baja. Hooks desactivados,
   sin configuración global ni del sistema.
5. **Consola restringida:** solo `node`, `npm`, `pnpm`, `python` y `git` de lectura, sin intérprete de comandos
   ni operadores de shell. Commits únicamente desde el panel Git (con autoría del socio).
6. **Live Preview** en un origen distinto (`:8001`): exige la sesión de WADIMA y pertenencia al área; el proxy no
   reenvía cookies al servidor de desarrollo; los WebSocket de la vista previa comprueban el `Origin`. El código
   de la vista previa no puede leer la cookie `httpOnly` ni el token CSRF de la aplicación.

**Limitaciones conocidas:**

- Sin aislamiento de red: el código puede salir a internet (lo necesita `npm install`) y conectar a puertos
  locales (PostgreSQL exige una contraseña que no puede leer).
- Puede **leer** (no modificar) archivos del usuario anfitrión que no estén etiquetados: documentos, `~\.ssh`,
  credenciales de otras herramientas. Para aislarlo por completo, ejecute WADIMA con una cuenta de Windows
  dedicada (crearla requiere administrador) o use Docker/WSL cuando estén disponibles.
- Los proyectos de Studio no están aislados entre sí (todos comparten el nivel Bajo).
- Un editor del área puede dejar código que se ejecute cuando otro socio pulse «dev» o «test».
- En Linux/macOS solo se aplican `rlimits`.

Las etiquetas no afectan a los procesos normales del usuario. Para retirarlas:
`icacls <ruta> /setintegritylevel M` (archivo) o `icacls <ruta> /setintegritylevel (OI)(CI)M` (directorio).

## Servidor MCP (Claude Code de cada socio)

- `/mcp` exige un token personal `wmcp_…` (Bearer); se guarda solo su SHA-256 y se puede revocar al instante.
- Las herramientas aplican los permisos de áreas del dueño del token, igual que la API. No hay herramientas que
  borren o modifiquen datos de colaboración: solo se puede entregar el resultado de las tareas propias.
- Se rechaza cualquier `Origin` no permitido y se valida el `Host` (protección contra *DNS rebinding* del SDK
  oficial). El contenido de documentos y memoria se marca como datos no confiables.
- WADIMA no almacena ni intermedia credenciales de claude.ai; cada socio inicia sesión en su propio Claude Code.

## Reuniones

- El audio viaja entre navegadores con WebRTC (cifrado DTLS-SRTP obligatorio en WebRTC); el servidor solo reenvía
  la señalización entre participantes de la misma sala, con el remitente fijado por el servidor. No se graba nada.
- Solo entran los miembros del área de reuniones habilitada (y administradores); cada socio una vez (otra ventana
  sustituye a la anterior); máximo 8 participantes; límite de tamaño y frecuencia de señales.

## Acceso remoto

Por defecto el backend (`8000`), la vista previa (`8001`), Vite (`5173`), los servidores de Studio (`5300-5399`) y
PostgreSQL (`5433`) escuchan solo en `127.0.0.1`. **No abra puertos del router.** Para los socios remotos:

1. Instale [Tailscale](https://tailscale.com) en el equipo servidor y en el de cada socio (el instalador de
   Windows requiere administrador) y únalos a la misma tailnet; restrinja el acceso con las ACL de Tailscale.
2. Publique WADIMA por HTTPS solo dentro de la tailnet con `tailscale serve`, apuntando a `127.0.0.1:8000` y,
   en otro puerto HTTPS, a `127.0.0.1:8001` (consulte `tailscale serve --help` para la sintaxis de su versión).
3. En `.env`: `WADIMA_PUBLIC_ORIGIN=https://<equipo>.<tailnet>.ts.net`,
   `WADIMA_PREVIEW_PUBLIC_ORIGIN=https://<equipo>.<tailnet>.ts.net:<puerto>`,
   `WADIMA_ALLOWED_ORIGINS=["https://<equipo>.<tailnet>.ts.net"]` y `WADIMA_COOKIE_SECURE=true`. Si usa Drive,
   registre el nuevo URI de redirección en Google y en `WADIMA_GOOGLE_REDIRECT_URI`.

Tailscale no está instalado en este equipo: estos pasos no se han probado aquí.

**Misma red local, sin VPN:** `python manage.py tls <ip-privada>` genera una autoridad local con restricciones de
nombre (solo vale para esa IP o nombre) y el certificado del servidor, y configura en `.env` HTTPS, `COOKIE_SECURE`
y los orígenes. Añada `WADIMA_BIND_HOST=<ip-privada>` (decisión explícita: abre WADIMA a la red local) y entregue a
cada socio `var/secrets/tls/ca.crt` para importarlo en su sistema o navegador. El firewall de Windows preguntará la
primera vez; permita solo redes privadas. Probado aquí: generación de la cadena y conexión HTTPS real verificando
la autoridad (`tests/test_tls.py`); no se ha probado desde otro equipo de la red.

**Reuniones entre redes distintas sin VPN:** hace falta un servidor TURN propio (p. ej. coturn) configurado con
`WADIMA_TURN_URLS` y `WADIMA_TURN_SECRET`. Sin él, la conexión directa puede fallar detrás de NAT; WADIMA lo indica
en la tarjeta del participante.

## Copias de seguridad y recuperación

- **Local completa:** `python manage.py db backup` (`pg_dump` en `var/backups/`) más la carpeta `var/blobs`.
  Restaurar: `pg_restore -h 127.0.0.1 -p 5433 -U wadima -d wadima --clean <archivo.dump>` y copiar `var/blobs`.
- **Desde Drive:** copia lógica diaria sin secretos (`Backups/`) → `restore-backup` en una base vacía (genera
  contraseñas temporales nuevas) y `rebuild-blobs` para recuperar los originales verificando su SHA-256.
  Cubierto por `test_recovery.py`.
- La clave maestra no viaja en las copias: sin ella, las API keys y tokens OAuth guardados no se pueden descifrar
  y cada socio debe volver a conectarlos.
- La copia lógica incluye la identidad de la organización (con el logotipo) y todo el control horario; los tokens
  MCP no se copian (cada socio crea uno nuevo). Probado en `test_recovery.py`.
