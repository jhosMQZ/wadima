from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time

import httpx2
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from wadima.tls import generate


def test_local_authority_is_name_constrained(tmp_path):
    paths = generate(tmp_path, ["192.168.50.10", "wadima.lan"], "VANTIA GROUP")
    assert set(paths) == {"ca_cert", "cert", "key"}  # la clave de la autoridad no se conserva
    ca = x509.load_pem_x509_certificate(paths["ca_cert"].read_bytes())
    cert = x509.load_pem_x509_certificates(paths["cert"].read_bytes())[0]
    assert cert.issuer == ca.subject and "VANTIA GROUP" in ca.subject.rfc4514_string()
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical
    permitted = constraints.value.permitted_subtrees
    assert x509.DNSName("wadima.lan") in permitted
    assert x509.IPAddress(ipaddress.ip_network("192.168.50.10/32")) in permitted
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert set(san.get_values_for_type(x509.DNSName)) == {"localhost", "wadima.lan"}
    assert {str(ip) for ip in san.get_values_for_type(x509.IPAddress)} == {"127.0.0.1", "192.168.50.10"}
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 398


@pytest.fixture
def https_server(app, tmp_path):
    paths = generate(tmp_path, ["127.0.0.1"])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        ssl_certfile=str(paths["cert"]),
        ssl_keyfile=str(paths["key"]),
    )
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port, paths["ca_cert"]
    server.should_exit = True
    thread.join(10)


def test_real_https_handshake_with_the_local_authority(https_server):
    port, ca = https_server
    ctx = ssl.create_default_context(cafile=str(ca))
    for host in ("127.0.0.1", "localhost"):
        with httpx2.Client(verify=ctx) as http:
            r = http.get(f"https://{host}:{port}/api/health")
            assert r.status_code == 200 and r.json()["database"] is True
    # Sin la autoridad local el certificado no es de confianza.
    with pytest.raises(httpx2.ConnectError), httpx2.Client() as http:
        http.get(f"https://127.0.0.1:{port}/api/health")
