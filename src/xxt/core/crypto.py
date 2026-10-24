"""凭据加密（AES-256-GCM，PLAN §5.6）。

主密钥来源（优先级）：环境变量 XXT_MASTER_KEY → data/secret.key（首次自动生成）。
密钥文件绝不入库/随程序分发；密文格式 = 12 字节 nonce + AES-GCM 密文。
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path


def master_key(data_dir: Path) -> bytes:
    """取主密钥（32 字节）。环境变量为任意口令的 sha256；密钥文件则直接使用。"""
    env = os.environ.get("XXT_MASTER_KEY")
    if env:
        return hashlib.sha256(env.encode()).digest()
    key_file = data_dir / "secret.key"
    if not key_file.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(secrets.token_bytes(32))
    raw = key_file.read_bytes()
    return raw if len(raw) == 32 else hashlib.sha256(raw).digest()


def encrypt(key: bytes, plaintext: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), None)


def decrypt(key: bytes, blob: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).decrypt(blob[:12], blob[12:], None).decode()
