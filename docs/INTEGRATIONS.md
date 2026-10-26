# Integraciones: Google Drive, Claude y otras IA

Estado al 2026-10-25: ambas integraciones están implementadas y probadas **contra servidores simulados**
(`backend/tests/fakedrive.py` y el transporte simulado de `test_ai.py`). Ninguna se ha conectado todavía a una
cuenta real porque faltan las credenciales; la interfaz las muestra como «no configurado» / «sin cuenta» y nunca
como conectadas sin haberlo comprobado.

## Google Drive

### Qué hace falta (una vez, el administrador)

1. En <https://console.cloud.google.com> cree un proyecto y habilite **Google Drive API**.
2. **Pantalla de consentimiento OAuth:** tipo «Externo», estado «Prueba», y añada como usuarios de prueba la
   cuenta de Google que alojará la carpeta `WADIMA` (y las de los socios si van a conectar la suya).
3. **Credenciales → ID de cliente OAuth → Aplicación web**, con este URI de redirección autorizado:
   `http://127.0.0.1:8000/api/drive/oauth/callback` (debe coincidir con `WADIMA_GOOGLE_REDIRECT_URI`).
4. En `.env` del servidor:
   ```
   WADIMA_GOOGLE_CLIENT_ID=...
   WADIMA_GOOGLE_CLIENT_SECRET=...
   ```
5. Reinicie WADIMA, entre como administrador y pulse **Conexiones → Google Drive → Conectar**. Google pedirá el
   consentimiento en su propia página; WADIMA nunca ve la contraseña de Google.
6. Pulse **Probar conexión**. Solo entonces la integración aparece como conectada.

Con `python manage.py dev` el retorno de Google llega a `:8000` y redirige a `:8000/connections`, que sirve el
frontend compilado (`python manage.py build`). Para volver a Vite, ponga `WADIMA_PUBLIC_ORIGIN` y el URI de
redirección en `http://127.0.0.1:5173` (Vite reenvía `/api` al backend) y registre ese URI también en Google.

### Alcance de permisos

- Por defecto `drive.file` (no sensible): WADIMA solo ve los archivos que él mismo creó. No requiere verificación
  de Google.
- `WADIMA_GOOGLE_FULL_SCOPE=true` pide `drive` (restringido): permite detectar archivos añadidos a mano en la
  carpeta, pero Google exige verificación de la aplicación para usarlo fuera del modo de prueba.

### Estructura y contenido

```
WADIMA/
  Workspace/  Areas/  Projects/<área>/  Library/<área>/  Processed/  Markdown/<área>/
  Memory/  Reports/  Backups/
```

| Se sube | Carpeta |
|---|---|
| Originales de la biblioteca (con `appProperties.wadimaRef` y SHA-256) | `Library/<área>/` |
| Markdown convertido | `Markdown/<área>/` |
| Notas (las de proyecto, por área) | `Workspace/<área>/` o `Projects/<área>/` |
| Resumen de actividad de cada proyecto | `Reports/<área>/` |
| Ancla de auditoría (hash cabeza, cada hora) | `Reports/audit-anchor.json` |
| Copia lógica diaria sin secretos | `Backups/wadima-backup-AAAA-MM-DD.json` |

Nunca se suben: hashes de contraseñas, sesiones, tokens OAuth, API keys ni la clave maestra.

### Sincronización

- Cada objeto tiene una fila `drive_files` con el ID estable de Drive y su estado:
  `pending → uploading → synced`, o `error`, `conflict`, `remote_deleted`. El indicador de la barra superior y la
  pantalla de Conexiones muestran estos estados reales.
- Subidas **reanudables** con verificación: el MD5 que devuelve Google debe coincidir con el local; si no,
  se reintenta. La sesión de subida se guarda para reanudarla tras una caída, y la búsqueda por `wadimaRef` evita
  duplicados si el proceso cayó entre la subida y el registro del ID.
- Errores de red y 5xx/429: reintentos con espera exponencial (hasta 8). Drive desconectado: los trabajos quedan
  pendientes sin consumir intentos y se reanudan al reconectar.
- Cambios hechos directamente en Drive: la reconciliación periódica (`changes.list`, cada
  `WADIMA_DRIVE_POLL_SECONDS`) los detecta. Si el local no cambió, se importan (las notas como revisión nueva);
  si cambiaron ambos, la fila queda en `conflict` y se resuelve con **Usar WADIMA**, **Usar Drive** o
  **Conservar ambas**. Las notificaciones push no se usan porque requieren un endpoint HTTPS público.

### Socios y permisos

La carpeta `WADIMA` pertenece a la cuenta que conectó el administrador; no se asume una unidad compartida.
**Conexiones** consulta en Drive quién tiene acceso a la carpeta y permite compartirla (**Compartir carpeta**, rol `writer`)
con el correo de Google que cada socio indique en **Configuración**. Dentro de WADIMA, los documentos siguen los
permisos por área, independientemente de los permisos de Drive.

## Claude

### Qué permite realmente cada opción (investigado el 25-10-2026 en la documentación oficial)

Fuentes: *Claude Code → Legal and compliance* (code.claude.com/docs/en/legal-and-compliance), *Agent SDK overview*
(code.claude.com/docs/en/agent-sdk/overview), *Claude Code → MCP* (code.claude.com/docs/en/mcp) y el centro de ayuda
de Claude sobre conectores personalizados (support.claude.com, artículo 11175166).

| Opción | ¿Permitida para WADIMA? | Detalle |
|---|---|---|
| Cuenta personal / suscripción **Pro o Max** usada por el servidor de WADIMA | **No** | «OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications.» «Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users… developers may not collect, store, or intermediate Claude.ai credentials or session tokens.» |
| Una suscripción Pro **incluye la API** | **No** | La API se contrata y factura aparte en Claude Console |
| **Agent SDK** con el inicio de sesión de claude.ai | **No** | «Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK.» |
| **Claude Code del socio** (iniciado con su suscripción) conectado a WADIMA como **servidor MCP** | **Sí** | El socio usa el binario oficial con su propia cuenta en su equipo («Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription»). WADIMA no ve ni guarda credenciales de claude.ai: Claude Code se conecta a `/mcp` con un token de WADIMA. Los límites anunciados de Pro/Max suponen un uso ordinario e individual («ordinary, individual usage») |
| **Conectores personalizados de claude.ai** (web/Desktop) | No en esta instalación | Anthropic se conecta a ellos desde su nube: exigiría publicar WADIMA en internet (según el centro de ayuda, un servidor detrás de una VPN o un cortafuegos no es accesible salvo que se permitan las IP de Anthropic). Claude Desktop admite servidores MCP locales por `claude_desktop_config.json` (stdio); WADIMA ofrece HTTP, así que la vía recomendada es Claude Code |
| **API de Anthropic** (API key propia) | **Sí** | Mecanismo oficial para aplicaciones; facturación por uso del socio |
| Importación manual de resultados | Sí | El socio pega en WADIMA lo obtenido en claude.ai o Claude Code |

WADIMA nunca automatiza la web de claude.ai, no extrae cookies ni reutiliza sesiones.

### Opción A: suscripción Pro/Max mediante Claude Code (MCP)

1. El socio instala Claude Code en su equipo e inicia sesión con su cuenta (flujo oficial de Anthropic).
2. En WADIMA: **Configuración de IA → Crear token**. El token (`wmcp_…`) se muestra una sola vez; WADIMA guarda
   solo su SHA-256. Máximo 5 activos por socio; se revocan al instante.
3. En su terminal ejecuta el comando que muestra WADIMA:

   ```
   claude mcp add --transport http wadima https://<ip-o-nombre-de-wadima>:8000/mcp --header "Authorization: Bearer wmcp_…"
   ```

   Para no escribir el token en un archivo compartido, un `.mcp.json` con
   `"headers": {"Authorization": "Bearer ${WADIMA_TOKEN}"}` y la variable `WADIMA_TOKEN` en su equipo.
4. En Claude Code, `/mcp` muestra el estado. WADIMA marca la conexión como **conectada solo cuando recibe una
   petición real** con ese token (y guarda el nombre y versión que declara el cliente).
5. Herramientas expuestas (siempre con los permisos de áreas del socio): `whoami`, `search_memory`,
   `list_projects`, `list_tasks`, `read_document`, `list_pending_tasks`, `get_task`, `submit_result`. Todo contenido
   de la biblioteca o la memoria se entrega como **datos no confiables**.
6. **Tareas para su Claude:** el socio encarga una tarea en WADIMA (área, proyecto, con o sin memoria); en Claude Code
   pide «atiende mis tareas de WADIMA»; Claude las lee con `get_task` y entrega el resultado con `submit_result`. El
   resultado queda privado en el Laboratorio de IA hasta que lo comparte. El modelo y los límites se gestionan en
   Claude Code (`/model`, `/usage`); WADIMA no puede consultarlos.

7. **Tareas de proyecto para la IA de un socio:** en una tarea, «IA asignada → Claude de X» la pone en la cola de X
   (solo si X ya conectó su Claude Code). Al recogerla (`get_task`) la tarea pasa a «en curso»; al entregarla
   (`submit_result`) el resultado se publica como comentario de la tarea, se comparte con el proyecto, la tarea
   queda terminada (o bloqueada si la IA informa de un fallo) y se avisa a quien la asignó. Quitar la IA,
   reasignarla, borrar la tarea o terminarla a mano cancela lo que estuviera en cola. Nadie usa la suscripción
   de otro: cada Claude solo trabaja cuando su dueño se lo pide.

Seguridad del endpoint: token Bearer obligatorio, rechazo de cualquier `Origin` no permitido y protección contra
*DNS rebinding* del SDK (lista de hosts). Desde otro equipo la dirección debe ser la IP privada o de VPN, nunca
`localhost`.

### Opción B: API de Anthropic (clave propia)

1. Cada socio crea su propia API key en <https://platform.claude.com> → API keys (requiere facturación de API).
2. En WADIMA: **Configuración de IA → API de Anthropic → Guardar y verificar**. La clave se cifra en el servidor y nunca se
   devuelve al navegador (solo una pista con los últimos caracteres).
3. La verificación llama a `GET /v1/models` (no consume tokens) y muestra los modelos disponibles y los límites
   de uso que informa la API. Solo con esa verificación superada aparece como conectada.
4. **Desconectar** borra la clave de WADIMA; para invalidarla del todo hay que revocarla en Claude Console.

### Uso

- **Agentes** (Laboratorio de IA): el socio escribe una petición; WADIMA recupera fragmentos y recuerdos de las
  áreas a las que tiene acceso, dentro de un presupuesto de tokens, y los envía como datos delimitados. La
  respuesta cita las fuentes `[n]` y queda enlazada a sus referencias. El modelo no recibe herramientas, así que
  un documento malicioso no puede ejecutar acciones.
- Modelo por defecto `claude-opus-5-5` (configurable por socio entre los que su clave permite) y nivel de
  esfuerzo `low`…`max`. Se registran el uso de tokens, el `request_id` y los límites devueltos por la API.
- En la vista de un documento, **contar tokens** usa el endpoint oficial `count_tokens` (medición, no estimación).
- **Privacidad:** cada ejecución es privada de su autor hasta que la comparte con un área o proyecto; solo
  entonces la ven los demás y pasa al Memory Agent. Las claves y ejecuciones de un socio nunca se usan para
  peticiones de otro (cubierto por `test_ai.py`).
- **Modo compatible sin API:** quien solo tenga suscripción puede usar claude.ai y pegar el resultado con
  **Importar resultado**; queda marcado como importado manualmente.

### Errores

Los errores de la API se traducen a mensajes comprensibles por tipo: clave inválida o revocada (la conexión pasa
a «error»), permisos, límite de uso (429 con `retry-after`), red, modelo inexistente, error del servidor y
negativa del modelo.

## Otras APIs de IA (OpenAI, Google Gemini, DeepSeek y compatibles con OpenAI)

Además de Claude, cada socio puede conectar **sus propias claves** de otros servicios en **Configuración de IA → Otras
APIs de IA**. Se aplican las mismas reglas que con Anthropic: la clave se cifra en el servidor, nunca vuelve al
navegador (solo una pista), solo se usa para las peticiones de su dueño y la conexión aparece como verificada
únicamente después de una comprobación real.

| Proveedor | Clave | Verificación (sin coste) | Preguntas | Recuento de tokens |
|---|---|---|---|---|
| **OpenAI** | `sk-…` de platform.openai.com (la suscripción de ChatGPT no incluye la API) | `GET /v1/models` | `POST /v1/chat/completions` | No disponible: WADIMA no lo estima |
| **Google Gemini** | clave de Google AI Studio | `GET /v1beta/models` | `POST /v1beta/models/{modelo}:generateContent` | `:countTokens` |
| **DeepSeek** | `sk-…` de platform.deepseek.com | `GET https://api.deepseek.com/models` | `POST https://api.deepseek.com/chat/completions` | No disponible |
| **Compatible con OpenAI** | la del servicio (OpenRouter, Groq, Mistral, Together…) | `GET {dirección}/models` | `POST {dirección}/chat/completions` | No disponible |

- **Modelos:** la lista sale de la propia cuenta al verificar la clave. Se ocultan los que no sirven para conversar
  (embeddings, audio, imagen), y el modelo por defecto se elige de esa lista; el socio puede cambiarlo.
- **Contexto e instrucciones:** igual que con Claude. Las fuentes de la memoria viajan como datos no confiables y el
  modelo no recibe herramientas. En Gemini, las partes de razonamiento interno (`thought`) no se muestran como
  respuesta.
- **Esfuerzo:** el selector `low`…`max` solo existe en Anthropic. En los demás no se envía límite de tokens ni
  parámetros de razonamiento: cada modelo aplica los suyos y así se evitan rechazos por parámetros no admitidos.
- **Errores:** clave inválida (en Gemini llega como `400 API_KEY_INVALID`), permisos, saldo insuficiente (DeepSeek:
  `402`), límite de uso (`429` con `retry-after`), modelo inexistente, red y negativas del modelo (`refusal` o
  `content_filter` en OpenAI; `SAFETY` y bloqueos del prompt en Gemini).
- **APIs compatibles y seguridad:** para que el servidor no se pueda usar para alcanzar la red interna, la dirección
  debe ser `https://` hacia un servicio público. Se rechazan IP privadas, de enlace local o de metadatos, nombres
  que resuelven a la red interna, credenciales en la URL y parámetros. `http://127.0.0.1` / `localhost` (Ollama,
  LM Studio en el propio equipo de WADIMA) solo puede configurarlo un administrador.
- **Probado aquí:** con servidores simulados que reproducen el formato documentado de cada API
  (`backend/tests/test_ai_providers.py`) y una prueba de navegador para las tarjetas y la validación local. Sin
  claves reales de esos servicios en este equipo, la conexión real con cada uno queda por comprobar con la clave
  de cada socio.
