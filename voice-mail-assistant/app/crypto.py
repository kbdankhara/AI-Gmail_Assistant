"""Encrypts secrets (App Passwords, API keys) before they reach the database."""
from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def init(key: str) -> None:
    global _fernet
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret could not be decrypted. Was ENCRYPTION_KEY changed?") from exc
