"""CLI de administración local: python -m wadima.cli <comando>."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from wadima import audit
from wadima.config import get_settings
from wadima.db import session_scope
from wadima.models import Area, AreaMember, User
from wadima.security.crypto import hash_password, random_token

DEFAULT_USERS = [
    ("socio1", "Socio 1", "admin"),
    ("socio2", "Socio 2", "member"),
    ("socio3", "Socio 3", "member"),
]
AVATARS = [
    {"skin": "#f1c7a1", "hair": "#3b2a20", "shirt": "#3f7cd8", "pants": "#2d3142", "style": 0},
    {"skin": "#c68a5e", "hair": "#141414", "shirt": "#d8613f", "pants": "#3a3a52", "style": 1},
    {"skin": "#e8b48f", "hair": "#a0522d", "shirt": "#3fa66b", "pants": "#2f2b3a", "style": 2},
]


def bootstrap_users() -> None:
    """Crea las tres cuentas iniciales con contraseñas temporales (cambio obligatorio al entrar)
    y el área «General». Las contraseñas se muestran una sola vez y se guardan en var/secrets."""
    settings = get_settings()
    created: list[tuple[str, str]] = []
    with session_scope() as db:
        users = []
        for i, (username, name, role) in enumerate(DEFAULT_USERS):
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                temp = random_token(12)
                user = User(
                    username=username,
                    display_name=name,
                    role=role,
                    password_hash=hash_password(temp),
                    must_change_password=True,
                    avatar=AVATARS[i],
                )
                db.add(user)
                db.flush()
                audit.record(
                    db,
                    "user.created",
                    target_type="user",
                    target_id=user.id,
                    data={"username": username, "role": role, "via": "bootstrap"},
                )
                created.append((username, temp))
            users.append(user)
        area = db.execute(select(Area).where(Area.slug == "general")).scalar_one_or_none()
        if area is None:
            area = Area(
                slug="general",
                name="General",
                room_key="ops",
                description="Área común de coordinación de los socios.",
                created_by=users[0].id,
            )
            db.add(area)
            db.flush()
            for u in users:
                db.add(AreaMember(area_id=area.id, user_id=u.id, role="owner", added_by=users[0].id))
            audit.record(
                db,
                "area.created",
                target_type="area",
                target_id=area.id,
                area_id=area.id,
                data={"name": "General", "via": "bootstrap"},
            )
    if created:
        settings.secrets_dir.mkdir(parents=True, exist_ok=True)
        out = settings.secrets_dir / "initial-passwords.txt"
        with out.open("a", encoding="utf-8") as fh:
            for username, temp in created:
                fh.write(f"{username}\t{temp}\n")
        print("Cuentas creadas (contraseña temporal; se exigirá cambiarla en el primer acceso):")
        for username, temp in created:
            print(f"  {username:10s} {temp}")
        print(f"Copia local: {out}  (bórrela tras entregar las contraseñas)")
    else:
        print("Las tres cuentas iniciales ya existen.")


def reset_password(username: str) -> None:
    with session_scope() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            sys.exit(f"No existe el usuario {username}")
        temp = random_token(12)
        user.password_hash = hash_password(temp)
        user.must_change_password = True
        audit.record(db, "user.password_reset", target_type="user", target_id=user.id, data={"via": "cli"})
    print(f"Contraseña temporal de {username}: {temp}")


def verify_audit() -> None:
    with session_scope() as db:
        result = audit.verify_chain(db)
    print(result)
    if not result["ok"]:
        sys.exit(1)


def restore_backup(path: str, force: bool) -> None:
    from wadima.backup import load_backup_file, restore_logical

    with session_scope() as db:
        result = restore_logical(db, load_backup_file(path), force=force)
    print("Restauración completada:", result["counts"])
    print("Contraseñas temporales (cambio obligatorio al entrar):")
    for username, temp in result["temporary_passwords"].items():
        print(f"  {username:10s} {temp}")


def rebuild_blobs() -> None:
    from wadima.backup import rebuild_blobs_from_drive

    with session_scope() as db:
        print(rebuild_blobs_from_drive(db))


def make_tls(hosts: list[str]) -> None:
    from wadima import org as orgsvc
    from wadima.tls import generate

    try:
        with session_scope() as db:
            org_name = orgsvc.get_org(db).legal_name
    except Exception:  # noqa: BLE001 - sin base de datos se usa el nombre por defecto
        org_name = "WADIMA"
    paths = generate(get_settings().secrets_dir / "tls", hosts, org_name)
    for name, path in paths.items():
        print(f"{name}\t{path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="wadima")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap-users")
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    sub.add_parser("verify-audit")
    rb = sub.add_parser("restore-backup", help="Restaura una copia lógica JSON (de Drive/Backups) en una base vacía")
    rb.add_argument("path")
    rb.add_argument("--force", action="store_true")
    sub.add_parser("rebuild-blobs", help="Recupera desde Drive los originales que faltan en var/blobs")
    tp = sub.add_parser("tls", help="Genera certificados HTTPS para servir WADIMA en una red privada")
    tp.add_argument("hosts", nargs="+", help="IP privada o nombre con el que los socios abrirán WADIMA")
    args = parser.parse_args(argv)
    if args.cmd == "bootstrap-users":
        bootstrap_users()
    elif args.cmd == "reset-password":
        reset_password(args.username)
    elif args.cmd == "verify-audit":
        verify_audit()
    elif args.cmd == "restore-backup":
        restore_backup(args.path, args.force)
    elif args.cmd == "rebuild-blobs":
        rebuild_blobs()
    elif args.cmd == "tls":
        make_tls(args.hosts)


if __name__ == "__main__":
    main()
