# -*- coding: utf-8 -*-
"""用户个人API凭据加密：只处理内存明文与版本化密文，不负责持久化。"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import config


_CIPHERTEXT_PREFIX = "ztpk1."
_NONCE_BYTES = 12
_AAD_PREFIX = b"zhitian:personal-deepseek-key:v1:"


class CredentialCryptoError(ValueError):
    """凭据加解密失败；错误文本不得包含明文或密文。"""


def encrypt_personal_deepseek_key(plaintext: str, user_id: str) -> str:
    """以AES-256-GCM加密个人Key，并把密文绑定到所属user_id。"""
    value = str(plaintext or "")
    owner = str(user_id or "").strip()
    if not value or not owner:
        raise CredentialCryptoError("个人API凭据或账号标识不能为空")
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = AESGCM(_master_key()).encrypt(
        nonce,
        value.encode("utf-8"),
        _associated_data(owner),
    )
    payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    return _CIPHERTEXT_PREFIX + payload


def decrypt_personal_deepseek_key(ciphertext: str, user_id: str) -> str:
    """解密所属账号的个人Key；篡改、错账号或错主密钥统一安全失败。"""
    value = str(ciphertext or "").strip()
    owner = str(user_id or "").strip()
    if not value.startswith(_CIPHERTEXT_PREFIX) or not owner:
        raise CredentialCryptoError("个人API凭据密文无效")
    try:
        packed = base64.b64decode(
            value[len(_CIPHERTEXT_PREFIX):].encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        if len(packed) <= _NONCE_BYTES:
            raise CredentialCryptoError("个人API凭据密文无效")
        plaintext = AESGCM(_master_key()).decrypt(
            packed[:_NONCE_BYTES],
            packed[_NONCE_BYTES:],
            _associated_data(owner),
        )
        return plaintext.decode("utf-8")
    except CredentialCryptoError:
        raise
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, InvalidTag, ValueError) as exc:
        raise CredentialCryptoError("个人API凭据密文无效") from exc


def is_personal_key_ciphertext(value: str) -> bool:
    """只判断版本前缀，供存储层拒绝把疑似明文误写入凭据列。"""
    return str(value or "").startswith(_CIPHERTEXT_PREFIX)


def _master_key() -> bytes:
    try:
        decoded = base64.b64decode(
            config.PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (AttributeError, UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialCryptoError("个人API凭据加密配置无效") from exc
    if len(decoded) != 32:
        raise CredentialCryptoError("个人API凭据加密配置无效")
    return decoded


def _associated_data(user_id: str) -> bytes:
    return _AAD_PREFIX + user_id.encode("utf-8")
