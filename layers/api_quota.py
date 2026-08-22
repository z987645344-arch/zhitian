# -*- coding: utf-8 -*-
"""用户API额度来源：企业授权、个人凭据状态与请求期解析。"""

import re
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field, SecretStr

import config
from layers import auth, credential_crypto, enterprise_password
from layers.db_transaction import transaction


ENTERPRISE_PASSWORD_MAX_FAILURES = 5
ENTERPRISE_PASSWORD_LOCK_HOURS = 12
SOURCE_ENTERPRISE = "enterprise"
SOURCE_PERSONAL = "personal"


logger = logging.getLogger(__name__)


class ApiQuotaStatus(BaseModel):
    source: Optional[str] = None
    enterprise_authorized: bool
    personal_key_configured: bool
    enterprise_password_attempts_remaining: int
    enterprise_password_locked_until: Optional[str] = None


class ResolvedApiCredential(BaseModel):
    """请求期凭据；SecretStr禁止repr显示，Field排除防止意外序列化。"""

    source: str
    api_key: SecretStr = Field(exclude=True, repr=False)


class ApiQuotaError(Exception):
    """额度来源业务错误基类；错误文本不得包含任何凭据内容。"""


class ApiQuotaAccountNotFoundError(ApiQuotaError):
    pass


class EnterprisePasswordInvalidError(ApiQuotaError):
    def __init__(self, attempts_remaining: int):
        super().__init__("企业密码不正确")
        self.attempts_remaining = max(0, int(attempts_remaining))


class EnterprisePasswordLockedError(ApiQuotaError):
    def __init__(self, locked_until: str):
        super().__init__("企业密码输入已锁定")
        self.locked_until = locked_until


class PersonalDeepSeekKeyInvalidError(ApiQuotaError):
    pass


class ApiQuotaSourceUnavailableError(ApiQuotaError):
    pass


class ApiQuotaNotConfiguredError(ApiQuotaError):
    pass


class ApiCredentialUnavailableError(ApiQuotaError):
    pass


def get_status(
    user_id: str,
    now: Optional[datetime] = None,
) -> ApiQuotaStatus:
    """返回可供前端展示的状态，永不读取或返回个人Key密文。"""
    current = _utc_now(now)
    with auth._connect() as conn:
        row = conn.execute(
            """
            SELECT api_quota_source, personal_deepseek_key_enc,
                   enterprise_api_authorized_at,
                   enterprise_password_fail_count,
                   enterprise_password_locked_until
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if not row:
        raise ApiQuotaAccountNotFoundError("账号不存在")

    locked_until = _parse_utc(row["enterprise_password_locked_until"])
    lock_active = bool(locked_until and locked_until > current)
    fail_count = int(row["enterprise_password_fail_count"] or 0)
    if not lock_active and locked_until:
        fail_count = 0
        locked_until = None
    remaining = max(0, ENTERPRISE_PASSWORD_MAX_FAILURES - fail_count)
    return ApiQuotaStatus(
        source=row["api_quota_source"],
        enterprise_authorized=bool(row["enterprise_api_authorized_at"]),
        personal_key_configured=bool(row["personal_deepseek_key_enc"]),
        enterprise_password_attempts_remaining=remaining,
        enterprise_password_locked_until=(
            locked_until.isoformat() if lock_active and locked_until else None
        ),
    )


def authorize_enterprise_source(
    user_id: str,
    supplied_password: str,
    now: Optional[datetime] = None,
) -> ApiQuotaStatus:
    """首次校验企业流动密码；成功授权永久保留并选中企业来源。"""
    current = _utc_now(now)
    expected_password = enterprise_password.get_current_enterprise_password()
    supplied = str(supplied_password or "")
    pending_error: Optional[ApiQuotaError] = None

    with transaction(auth.USERS_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT enterprise_api_authorized_at,
                   enterprise_password_fail_count,
                   enterprise_password_locked_until
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            raise ApiQuotaAccountNotFoundError("账号不存在")

        # 一次验证后永久授权：后续业务日轮换或手工刷新不撤销该状态。
        if row["enterprise_api_authorized_at"]:
            conn.execute(
                "UPDATE users SET api_quota_source = ? WHERE user_id = ?",
                (SOURCE_ENTERPRISE, user_id),
            )
        else:
            locked_until = _parse_utc(row["enterprise_password_locked_until"])
            if locked_until and locked_until > current:
                pending_error = EnterprisePasswordLockedError(
                    locked_until.isoformat()
                )
            else:
                fail_count = int(row["enterprise_password_fail_count"] or 0)
                if locked_until and locked_until <= current:
                    fail_count = 0

                if secrets.compare_digest(supplied, expected_password):
                    conn.execute(
                        """
                        UPDATE users
                        SET api_quota_source = ?, enterprise_api_authorized_at = ?,
                            enterprise_password_fail_count = 0,
                            enterprise_password_locked_until = NULL
                        WHERE user_id = ?
                        """,
                        (SOURCE_ENTERPRISE, current.isoformat(), user_id),
                    )
                else:
                    fail_count += 1
                    if fail_count >= ENTERPRISE_PASSWORD_MAX_FAILURES:
                        locked_until = current + timedelta(
                            hours=ENTERPRISE_PASSWORD_LOCK_HOURS
                        )
                        conn.execute(
                            """
                            UPDATE users
                            SET enterprise_password_fail_count = ?,
                                enterprise_password_locked_until = ?
                            WHERE user_id = ?
                            """,
                            (
                                ENTERPRISE_PASSWORD_MAX_FAILURES,
                                locked_until.isoformat(),
                                user_id,
                            ),
                        )
                        pending_error = EnterprisePasswordLockedError(
                            locked_until.isoformat()
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE users
                            SET enterprise_password_fail_count = ?,
                                enterprise_password_locked_until = NULL
                            WHERE user_id = ?
                            """,
                            (fail_count, user_id),
                        )
                        pending_error = EnterprisePasswordInvalidError(
                            ENTERPRISE_PASSWORD_MAX_FAILURES - fail_count
                        )

    # 失败计数和锁定状态必须先提交，再把业务错误交给API层映射；若在事务内
    # 直接raise，transaction()会回滚，表面报错却永远累计不到第5次。
    if pending_error:
        raise pending_error

    return get_status(user_id, now=current)


def save_personal_source(user_id: str, plaintext_key: str) -> ApiQuotaStatus:
    """校验并加密保存个人Key，同时显式选中个人额度来源。"""
    normalized = str(plaintext_key or "").strip()
    if not _is_valid_personal_deepseek_key(normalized):
        raise PersonalDeepSeekKeyInvalidError("个人DeepSeek Key格式无效")
    encrypted = credential_crypto.encrypt_personal_deepseek_key(
        normalized, user_id
    )
    if not credential_crypto.is_personal_key_ciphertext(encrypted):
        raise PersonalDeepSeekKeyInvalidError("个人DeepSeek Key保存失败")
    with transaction(auth.USERS_DB_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET personal_deepseek_key_enc = ?, api_quota_source = ?
            WHERE user_id = ?
            """,
            (encrypted, SOURCE_PERSONAL, user_id),
        )
        if cursor.rowcount < 1:
            raise ApiQuotaAccountNotFoundError("账号不存在")
    return get_status(user_id)


def clear_personal_source(user_id: str) -> ApiQuotaStatus:
    """清除个人Key；若当前选中个人来源则回到未配置，不自动回退企业。"""
    with transaction(auth.USERS_DB_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET personal_deepseek_key_enc = NULL,
                api_quota_source = CASE
                    WHEN api_quota_source = ? THEN NULL
                    ELSE api_quota_source
                END
            WHERE user_id = ?
            """,
            (SOURCE_PERSONAL, user_id),
        )
        if cursor.rowcount < 1:
            raise ApiQuotaAccountNotFoundError("账号不存在")
    return get_status(user_id)


def select_source(user_id: str, source: str) -> ApiQuotaStatus:
    """只允许手动选择已经完成授权/配置的来源，不做自动降级。"""
    normalized = str(source or "").strip().lower()
    if normalized not in {SOURCE_ENTERPRISE, SOURCE_PERSONAL}:
        raise ApiQuotaSourceUnavailableError("额度来源无效")
    with transaction(auth.USERS_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT enterprise_api_authorized_at, personal_deepseek_key_enc
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            raise ApiQuotaAccountNotFoundError("账号不存在")
        if normalized == SOURCE_ENTERPRISE and not row["enterprise_api_authorized_at"]:
            raise ApiQuotaSourceUnavailableError("请先验证企业流动密码")
        if normalized == SOURCE_PERSONAL and not row["personal_deepseek_key_enc"]:
            raise ApiQuotaSourceUnavailableError("请先配置个人DeepSeek Key")
        conn.execute(
            "UPDATE users SET api_quota_source = ? WHERE user_id = ?",
            (normalized, user_id),
        )
    return get_status(user_id)


def resolve_api_credential(user_id: str) -> ResolvedApiCredential:
    """按用户明确选择解析请求期Key；任何状态异常均不自动回退。"""
    with auth._connect() as conn:
        row = conn.execute(
            """
            SELECT api_quota_source, personal_deepseek_key_enc,
                   enterprise_api_authorized_at
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if not row:
        raise ApiQuotaAccountNotFoundError("账号不存在")

    source = row["api_quota_source"]
    if not source:
        raise ApiQuotaNotConfiguredError("尚未选择API额度来源")

    if source == SOURCE_ENTERPRISE:
        if not row["enterprise_api_authorized_at"]:
            raise ApiQuotaSourceUnavailableError("企业额度来源尚未授权")
        api_key = str(config.DEEPSEEK_API_KEY or "").strip()
        if not api_key:
            raise ApiCredentialUnavailableError("企业模型服务暂不可用")
        return ResolvedApiCredential(
            source=SOURCE_ENTERPRISE,
            api_key=SecretStr(api_key),
        )

    if source == SOURCE_PERSONAL:
        encrypted = row["personal_deepseek_key_enc"]
        if not encrypted:
            raise ApiQuotaSourceUnavailableError("个人额度来源尚未配置")
        try:
            api_key = credential_crypto.decrypt_personal_deepseek_key(
                str(encrypted), user_id
            )
        except credential_crypto.CredentialCryptoError as exc:
            logger.error(
                "个人DeepSeek Key解密失败：user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
            raise ApiCredentialUnavailableError("个人模型服务凭据不可用") from None
        return ResolvedApiCredential(
            source=SOURCE_PERSONAL,
            api_key=SecretStr(api_key),
        )

    raise ApiQuotaSourceUnavailableError("额度来源状态无效")


def _is_valid_personal_deepseek_key(value: str) -> bool:
    """只做本地形状校验，不发起付费请求，也不在错误中回显输入。"""
    return bool(re.fullmatch(r"sk-[A-Za-z0-9_-]{16,253}", value))


def _utc_now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
