"""Configuración de WADIMA leída de variables de entorno y del .env local (nunca versionado)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WADIMA_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"  # development | production | test
    database_url: str = "postgresql+psycopg://wadima@127.0.0.1:5433/wadima"
    # Rol propietario de las tablas, solo para migraciones. La aplicación usa un rol sin propiedad
    # (wadima_app) que no puede desactivar los triggers de inmutabilidad. Vacío = database_url.
    migration_database_url: str = ""
    test_database_url: str = ""
    test_app_database_url: str = ""
    data_dir: Path = REPO_ROOT / "var"
    home: Path = Path.home() / ".wadima"  # PostgreSQL portátil, sus datos y su contraseña (manage.py)
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    # Red: por defecto solo loopback. Para socios remotos, usar la IP de una VPN privada (Tailscale).
    bind_host: str = "127.0.0.1"
    public_origin: str = "http://127.0.0.1:8000"
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    cookie_secure: bool = False  # True cuando se sirve por HTTPS (p. ej. tailscale serve)
    # HTTPS directo (python manage.py tls <ip-o-nombre>): necesario para el micrófono desde otros equipos.
    tls_cert_file: str = ""
    tls_key_file: str = ""

    # Sesiones
    session_idle_minutes: int = 12 * 60
    session_absolute_hours: int = 7 * 24
    area_inactivity_minutes: int = 30  # cierre automático de entrada a área sin conexión

    # Límites de login
    login_max_failures: int = 5
    login_window_minutes: int = 15

    # Biblioteca
    max_upload_mb: int = 50
    max_unzipped_mb: int = 300

    # Clave maestra para cifrar secretos locales (OAuth, API keys). Si no se define, se genera y se
    # protege con DPAPI (Windows) en data_dir/secrets.
    master_key: str = ""

    # Google Drive (OAuth 2.0 de aplicación de escritorio/web, credenciales del proyecto de Google Cloud)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/drive/oauth/callback"
    google_api_base: str = "https://www.googleapis.com"
    google_oauth_base: str = "https://oauth2.googleapis.com"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    drive_poll_seconds: int = 60
    # drive.file (por defecto, no sensible) solo ve archivos creados por WADIMA. drive (restringido) permite
    # detectar archivos añadidos directamente en Drive, pero exige verificación de Google para uso amplio.
    google_full_scope: bool = False

    # Anthropic
    anthropic_base_url: str = "https://api.anthropic.com"

    # Studio / Live Preview
    preview_port: int = 8001
    preview_public_origin: str = "http://127.0.0.1:8001"
    studio_port_range_start: int = 5300
    studio_port_range_end: int = 5399
    run_memory_mb: int = 1024
    run_cpu_percent: int = 50
    run_timeout_seconds: int = 900
    run_max_processes: int = 64

    # Reuniones (WebRTC). Sin servidores ICE solo se conectan equipos con ruta directa entre sí (misma red local
    # o VPN privada como Tailscale). Ejemplo STUN: [{"urls": "stun:stun.example.org:3478"}]. Con un TURN propio
    # (coturn con use-auth-secret) indique sus URLs y el secreto compartido: WADIMA genera credenciales temporales.
    ice_servers: list[dict] = Field(default_factory=list)
    turn_urls: list[str] = Field(default_factory=list)
    turn_secret: str = ""
    turn_ttl_seconds: int = 6 * 3600

    # MCP: clientes oficiales de Claude (Claude Code / Claude Desktop) de cada socio consultan WADIMA en /mcp
    mcp_enabled: bool = True

    # Procesos de fondo dentro del backend (worker de documentos, Memory Agent, sincronización)
    embedded_workers: bool = True

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def studio_dir(self) -> Path:
        return self.data_dir / "studio"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
