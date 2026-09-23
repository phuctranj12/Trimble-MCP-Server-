from cryptography.fernet import Fernet


class Encryptor:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def decrypt(self, blob: bytes) -> str:
        return self._fernet.decrypt(blob).decode()
