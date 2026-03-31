"""
Generate a self-signed TLS certificate for EcranDistant.

Usage:
    python generate_cert.py

Produces:
    cert.pem  — certificate (share with nothing; used by server)
    key.pem   — private key  (keep secret)

Requires:  pip install cryptography
"""
import datetime
import ipaddress
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate():
    # ── Private key ────────────────────────────────────────────────────────
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # ── Certificate ────────────────────────────────────────────────────────
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'EcranDistant'),
    ])

    local_ip = socket.gethostbyname(socket.gethostname())

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
                x509.IPAddress(ipaddress.IPv4Address(local_ip)),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # ── Write files ────────────────────────────────────────────────────────
    with open('cert.pem', 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open('key.pem', 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    print('cert.pem and key.pem generated successfully.')
    print(f'Valid for: localhost, 127.0.0.1, {local_ip}')
    print('Valid for: 10 years')


if __name__ == '__main__':
    generate()
