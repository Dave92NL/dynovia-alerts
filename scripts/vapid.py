"""Generate the VAPID key pair for web push. Run once.

    py -3.12 scripts/vapid.py

The public key is not a secret - it ends up in meta.json and in the page. The
private key is, and belongs in GitHub Secrets and in .env, nowhere else.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private = key.private_numbers().private_value.to_bytes(32, "big")

    print("VAPID_PUBLIC_KEY=" + b64(public))
    print("VAPID_PRIVATE_KEY=" + b64(private))


if __name__ == "__main__":
    main()
