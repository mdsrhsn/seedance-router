"""Encrypt and decrypt API keys so they never sit in the database as plaintext."""

from cryptography.fernet import Fernet, InvalidToken

import config

_fernet = Fernet(config.FERNET_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt(token: bytes) -> str:
    try:
        return _fernet.decrypt(bytes(token)).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt a stored API key. This usually means FERNET_KEY "
            "changed since the credential was saved. Re-add the credential."
        ) from exc


def mask(plaintext: str) -> str:
    """Show enough of a key to recognise it, never enough to use it."""
    if len(plaintext) <= 8:
        return "*" * len(plaintext)
    return f"{plaintext[:4]}{'*' * 12}{plaintext[-4:]}"
