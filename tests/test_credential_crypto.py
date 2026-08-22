# -*- coding: utf-8 -*-
"""个人API凭据加密的版本格式、账号绑定与启动配置测试。"""

import base64
import os
import subprocess
import sys

import pytest

import config
from layers import credential_crypto


def _personal_key() -> str:
    return "s" + "k-" + "test_personal_key_material_123456"


def test_personal_key_round_trip_never_embeds_plaintext(monkeypatch):
    monkeypatch.setattr(
        config,
        "PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"unit-test-key-material-32-bytes!").decode("ascii"),
    )
    plaintext = _personal_key()

    encrypted = credential_crypto.encrypt_personal_deepseek_key(plaintext, "user-a")

    assert encrypted.startswith("ztpk1.")
    assert plaintext not in encrypted
    assert credential_crypto.is_personal_key_ciphertext(encrypted) is True
    assert credential_crypto.decrypt_personal_deepseek_key(encrypted, "user-a") == plaintext


def test_personal_key_ciphertext_is_bound_to_user_and_detects_tampering():
    encrypted = credential_crypto.encrypt_personal_deepseek_key(
        _personal_key(), "user-a"
    )

    with pytest.raises(credential_crypto.CredentialCryptoError):
        credential_crypto.decrypt_personal_deepseek_key(encrypted, "user-b")

    replacement = "A" if encrypted[-1] != "A" else "B"
    with pytest.raises(credential_crypto.CredentialCryptoError):
        credential_crypto.decrypt_personal_deepseek_key(
            encrypted[:-1] + replacement, "user-a"
        )


@pytest.mark.parametrize(
    "configured_value, expected_message",
    [
        ("", "must be configured"),
        ("not-base64", "must be URL-safe Base64"),
        (
            base64.urlsafe_b64encode(b"too-short").decode("ascii"),
            "must decode to exactly 32 bytes",
        ),
    ],
)
def test_invalid_personal_key_master_secret_rejects_config_import(
    configured_value, expected_message
):
    environment = os.environ.copy()
    environment["PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY"] = configured_value
    result = subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert expected_message in result.stderr
