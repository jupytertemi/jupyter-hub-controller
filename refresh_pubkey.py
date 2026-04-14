#!/usr/bin/env python3
"""
Refresh HAProxy pubkey.pem from cloud backend JWKS endpoint.

Runs on every boot via entrypoint_migrate.sh to ensure the JWT
verification key matches the cloud backend's signing key.

This prevents "Invalid JWT signature" rejection after hard resets
or key rotations.
"""
import os
import sys
import json
import base64
import struct

PUBKEY_PATH = "/root/jupyter-container/haproxy/pem/pubkey.pem"
ENV_FILE = "/root/jupyter-hub-controller/.env"


def get_jupyter_host():
    """Read JUPYTER_HOST from .env file."""
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("JUPYTER_HOST="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def fetch_jwks(host):
    """Fetch JWKS from cloud backend."""
    import urllib.request
    url = f"{host}/.well-known/jwks.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SecureProtect-Hub/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[refresh_pubkey] Failed to fetch JWKS from {url}: {e}")
        return None


def int_to_bytes(n):
    """Convert integer to bytes (big-endian, minimal length)."""
    length = (n.bit_length() + 7) // 8
    return n.to_bytes(length, byteorder='big')


def der_length(length):
    """Encode ASN.1 DER length."""
    if length < 0x80:
        return bytes([length])
    elif length < 0x100:
        return bytes([0x81, length])
    else:
        return bytes([0x82, (length >> 8) & 0xff, length & 0xff])


def der_integer(value_bytes):
    """Encode ASN.1 DER INTEGER."""
    # Add leading zero if high bit set (positive number convention)
    if value_bytes[0] & 0x80:
        value_bytes = b'\x00' + value_bytes
    content = value_bytes
    return b'\x02' + der_length(len(content)) + content


def jwk_to_pem(jwk):
    """Convert JWK RSA public key to PEM format using pure Python."""
    # Decode n and e from base64url
    n_b64 = jwk["n"]
    e_b64 = jwk["e"]

    # Add padding
    n_b64 += "=" * (4 - len(n_b64) % 4) if len(n_b64) % 4 else ""
    e_b64 += "=" * (4 - len(e_b64) % 4) if len(e_b64) % 4 else ""

    n_bytes = base64.urlsafe_b64decode(n_b64)
    e_bytes = base64.urlsafe_b64decode(e_b64)

    # Build RSA public key DER
    # RSAPublicKey ::= SEQUENCE { modulus INTEGER, publicExponent INTEGER }
    n_der = der_integer(n_bytes)
    e_der = der_integer(e_bytes)
    rsa_key_content = n_der + e_der
    rsa_key = b'\x30' + der_length(len(rsa_key_content)) + rsa_key_content

    # Wrap in BIT STRING
    bit_string_content = b'\x00' + rsa_key  # leading zero byte for BIT STRING
    bit_string = b'\x03' + der_length(len(bit_string_content)) + bit_string_content

    # Algorithm identifier for RSA: OID 1.2.840.113549.1.1.1 + NULL
    # SEQUENCE { OID, NULL }
    rsa_oid = b'\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01'
    null = b'\x05\x00'
    algo_content = rsa_oid + null
    algo_id = b'\x30' + der_length(len(algo_content)) + algo_content

    # SubjectPublicKeyInfo ::= SEQUENCE { algorithm, subjectPublicKey }
    spki_content = algo_id + bit_string
    spki = b'\x30' + der_length(len(spki_content)) + spki_content

    # Encode as PEM
    b64 = base64.b64encode(spki).decode()
    lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
    pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"
    return pem


def main():
    host = get_jupyter_host()
    if not host:
        print("[refresh_pubkey] JUPYTER_HOST not found in .env, skipping")
        return

    print(f"[refresh_pubkey] Fetching JWKS from {host}")
    jwks = fetch_jwks(host)
    if not jwks or "keys" not in jwks or len(jwks["keys"]) == 0:
        print("[refresh_pubkey] No keys in JWKS response, keeping existing pubkey.pem")
        return

    # Use first RSA key
    rsa_key = None
    for key in jwks["keys"]:
        if key.get("kty") == "RSA":
            rsa_key = key
            break

    if not rsa_key:
        print("[refresh_pubkey] No RSA key found in JWKS, keeping existing pubkey.pem")
        return

    try:
        new_pem = jwk_to_pem(rsa_key)
    except Exception as e:
        print(f"[refresh_pubkey] Failed to convert JWK to PEM: {e}")
        return

    # Check if pubkey changed
    old_pem = ""
    if os.path.exists(PUBKEY_PATH):
        with open(PUBKEY_PATH) as f:
            old_pem = f.read()

    if new_pem.strip() == old_pem.strip():
        print("[refresh_pubkey] pubkey.pem already matches JWKS, no change needed")
        return

    # Write new pubkey
    os.makedirs(os.path.dirname(PUBKEY_PATH), exist_ok=True)
    with open(PUBKEY_PATH, "w") as f:
        f.write(new_pem)
    print(f"[refresh_pubkey] Updated pubkey.pem from JWKS ({len(new_pem)} bytes)")

    # Restart HAProxy to pick up new key
    os.system("docker restart haproxy-service 2>/dev/null || true")
    print("[refresh_pubkey] Restarted HAProxy with new pubkey")


if __name__ == "__main__":
    main()
