"""Certificados HTTPS para servir WADIMA en una red privada (LAN o VPN) sin servicios externos.

Los navegadores solo permiten el micrófono en contextos seguros (HTTPS o localhost), así que para que los
socios se unan a las reuniones desde otros equipos hace falta HTTPS. Se genera una autoridad local con
*restricciones de nombre*: solo puede firmar certificados para los nombres e IP indicados, de modo que, si su
clave se filtrara, no serviría para suplantar otros sitios. Cada socio importa `ca.crt` (pública) en su equipo.
Con Tailscale no hace falta: `tailscale serve` ya ofrece HTTPS con certificados válidos.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _names(hosts: list[str]) -> tuple[list[x509.GeneralName], list[x509.GeneralName]]:
    """(SAN del servidor, subárboles permitidos de la CA)."""
    san: list[x509.GeneralName] = []
    permitted: list[x509.GeneralName] = []
    for h in dict.fromkeys(["localhost", "127.0.0.1", *hosts]):
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            san.append(x509.DNSName(h))
            permitted.append(x509.DNSName(h))
        else:
            san.append(x509.IPAddress(ip))
            permitted.append(x509.IPAddress(ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}")))
    return san, permitted


def generate(out_dir: Path, hosts: list[str], org_name: str = "WADIMA", days: int = 825) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    san, permitted = _names(hosts)

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name[:64]),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{org_name[:40]} - autoridad local"),
        ]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0] if hosts else "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=min(days, 397)))  # límite aceptado por los navegadores
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    pem = serialization.Encoding.PEM
    # La clave de la autoridad no se guarda: para cambiar los nombres se genera una cadena nueva.
    paths = {"ca_cert": out_dir / "ca.crt", "cert": out_dir / "server.crt", "key": out_dir / "server.key"}
    no_enc = serialization.NoEncryption()
    pkcs8 = serialization.PrivateFormat.PKCS8
    paths["ca_cert"].write_bytes(ca.public_bytes(pem))
    paths["cert"].write_bytes(cert.public_bytes(pem) + ca.public_bytes(pem))
    paths["key"].write_bytes(key.private_bytes(pem, pkcs8, no_enc))
    return paths
