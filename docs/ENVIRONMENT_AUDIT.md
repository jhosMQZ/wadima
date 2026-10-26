# Auditoría del entorno (Fase 0)

Fecha del escaneo: 2026-10-24/25. Todos los datos proceden de comandos ejecutados en este equipo; no se
muestran credenciales ni valores de variables de entorno.

## Sistema

| Elemento | Resultado |
|---|---|
| SO | Windows 11 Home Single Language 10.0.26300 (64 bits) |
| CPU | AMD Ryzen 3 3250U, 2 núcleos / 4 hilos |
| RAM | 7,7 GB totales, ~1,3 GB libres al escanear (equipo con poca memoria libre) |
| Disco | C: ~280 GB libres |
| Privilegios | Sesión **no elevada** (sin administrador) |
| Virtualización | Hipervisor presente; WSL2 habilitado **sin distribuciones** |
| Red | Salida HTTPS operativa hacia googleapis.com, api.anthropic.com, registry.npmjs.org, pypi.org |
| Puertos | 3000/5173/5432/5433/8000/8001 libres al inicio |

## Runtimes y herramientas

| Herramienta | Estado |
|---|---|
| Node.js | v24.19.0 · npm 11.17.0 · pnpm 10.33.0 |
| Python | 3.12.10 · pip 25.0.1 · **uv 0.11.7** (gestor del entorno del backend) |
| Git | 2.55.0 (sin identidad global configurada) |
| GitHub CLI | 2.101.0 |
| .NET | SDK presente (dotnet) |
| Java, Go, Rust, GCC/G++, PHP | **No instalados** (el editor los resalta; no se pueden ejecutar) |
| Docker / Podman | **No instalados** |
| PostgreSQL | **No instalado** → se instaló PostgreSQL 17.11 portátil (binarios oficiales EDB, sin admin) |
| Tesseract OCR | **No instalado** (el OCR queda como capacidad opcional) |
| Tailscale | **No instalado** |
| Navegadores | Chrome 155, Edge 155; caché de Playwright con Chromium |
| Codex CLI | 0.154.0, sesión iniciada con ChatGPT (no se usó para ejecutar trabajo) |
| Claude Code | 2.1.310 (herramienta de este desarrollo; WADIMA no reutiliza su sesión) |

## Extensiones de VS Code

**Instaladas y operativas (antes del escaneo):** anthropic.claude-code, ms-python.python, ms-python.vscode-pylance,
ms-python.debugpy, ms-python.vscode-python-envs, ms-vscode.powershell, mechatroner.rainbow-csv,
ms-ceintl.vscode-language-pack-es, pkief.material-icon-theme, kisstkondoros.vscode-gutter-preview,
oderwat.indent-rainbow, saoudrizwan.claude-dev, whizkydee.material-palenight-theme.

**Faltaban y se instalaron** (identificadores oficiales verificados por el propio instalador):

| Extensión | Motivo |
|---|---|
| dbaeumer.vscode-eslint 3.0.34 | Lint de TypeScript/React |
| esbenp.prettier-vscode 12.4.0 | Formato del frontend |
| charliermarsh.ruff 2026.90.0 | Lint/formato Python |
| ms-playwright.playwright 1.1.19 | Pruebas E2E y depuración del navegador |
| redhat.vscode-yaml 2.0.0 | YAML |
| editorconfig.editorconfig 0.18.2 | Convenciones del repositorio |

**No necesarias, incompatibles o a revisar:** golang.go y oracle.oracle-java (no hay toolchain Go/Java instalado);
ms-vscode-remote.remote-containers (no hay Docker); ritwickdey.liveserver (duplica el Live Preview de Vite; marcada
como no recomendada en `.vscode/extensions.json`); tonybaloney.vscode-pets y ttoowa.in-your-face-incredible (no
aportan al proyecto). No se desinstaló nada.

## Decisiones derivadas

- **PostgreSQL portátil** en `%USERPROFILE%\.wadima\tools\pgsql`, datos en `%USERPROFILE%\.wadima\pgdata`,
  puerto 5433, escuchando solo en 127.0.0.1, autenticación scram-sha-256. Gestionado con `python manage.py db ...`.
  La extensión **pgvector no está incluida** en los binarios de Windows y no hay compilador para construirla.
- **Sin Docker/Podman**: la ejecución de código de Project Studio usa *Job Objects* de Windows (límites de memoria,
  CPU, número de procesos y tiempo), un token de integridad Baja con etiquetas «no read up» sobre los datos del
  servidor (añadido en la sesión 2) y un entorno saneado. Limitaciones documentadas en `SECURITY.md`.
- **Poca RAM**: un solo proceso de backend (workers embebidos), Phaser con renderer Canvas y bucle que se duerme
  sin actividad, CodeMirror en lugar de Monaco.
- **Sin Tesseract**: los PDF escaneados se marcan como «requiere OCR»; el OCR se activa si Tesseract aparece en el PATH.

## Comandos usados (resumen)

`Get-CimInstance Win32_OperatingSystem/Win32_Processor`, `Get-PSDrive`, `Get-Command <herramienta>`,
`<herramienta> --version`, `code --list-extensions --show-versions`, `wsl --status`, `Get-Service`,
`Test-NetConnection <host> -Port 443`, `Get-NetTCPConnection -State Listen`, `codex login status`,
`code --install-extension <id>`, descarga de `postgresql-17.11-windows-x64-binaries.zip` desde enterprisedb.com.

## Riesgos y bloqueos identificados

1. **Google Drive**: requiere credenciales OAuth (Client ID/Secret) de un proyecto de Google Cloud y el
   consentimiento interactivo de una cuenta. No existen en este equipo → integración implementada y probada contra
   un servidor simulado; conexión real pendiente.
2. **Claude**: cada socio necesita su propia API key de Anthropic (la suscripción de claude.ai no incluye API).
   Ninguna disponible → integración implementada y probada con respuestas simuladas; conexión real pendiente.
3. **Acceso remoto de los socios**: sin Tailscale/VPN; el backend escucha solo en 127.0.0.1.
4. **Aislamiento de ejecución** más débil que un contenedor: sin aislamiento de red y con lectura de los archivos no etiquetados del usuario anfitrión.
