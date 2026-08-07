from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet

from backend.config import settings


class TokenCipher:
    def __init__(self) -> None:
        digest = hashlib.sha256(settings.session_secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")


def new_session_id() -> str:
    return secrets.token_urlsafe(36)


def new_oauth_state() -> str:
    return secrets.token_urlsafe(28)


token_cipher = TokenCipher()
