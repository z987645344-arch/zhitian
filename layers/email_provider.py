# -*- coding: utf-8 -*-
"""DirectMail 验证码邮件发送层：仅负责调用外部邮件服务，不处理验证码存储。"""

import time
from typing import Tuple

import config
from utils.logger import get_logger

logger = get_logger("email_provider")


class EmailServiceUnavailableError(RuntimeError):
    """邮件服务配置缺失或 SDK 不可用。"""


def _mail_subject_and_body(code: str, purpose: str) -> Tuple[str, str]:
    # customer自助注册与企业角色申请的文案必须可区分，避免收件人混淆两类流程
    if purpose == "customer_register":
        title = "客户注册"
        scene = "知天客户注册"
    elif purpose == "register":
        title = "注册验证"
        scene = "知天注册验证"
    elif purpose == "reset_password":
        title = "密码重置验证"
        scene = "知天密码重置验证"
    else:
        raise ValueError("验证码用途无效")
    subject = "知天%s验证码" % title
    body = (
        "<p>您正在进行%s。</p>"
        "<p>验证码：<strong>%s</strong></p>"
        "<p>验证码 5 分钟内有效，请勿向他人泄露。</p>" % (scene, code)
    )
    return subject, body


def _send_once(to_email: str, code: str, purpose: str) -> None:
    """调用官方 Tea SDK；延迟导入使缺少 SDK 时返回可识别错误。"""
    try:
        from alibabacloud_dm20151123.client import Client as DirectMailClient
        from alibabacloud_dm20151123 import models as dm_models
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise EmailServiceUnavailableError("DirectMail SDK 未安装") from exc

    client_config = open_api_models.Config(
        access_key_id=config.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=config.ALIYUN_ACCESS_KEY_SECRET,
    )
    client_config.region_id = config.ALIYUN_MAIL_REGION_ID
    client = DirectMailClient(client_config)
    subject, html_body = _mail_subject_and_body(code, purpose)
    request = dm_models.SingleSendMailRequest(
        account_name=config.ALIYUN_MAIL_ACCOUNT_NAME,
        address_type=1,
        to_address=to_email,
        subject=subject,
        html_body=html_body,
        reply_to_address=False,
    )
    client.single_send_mail(request)


def _is_retryable_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def send_verification_email(to_email: str, code: str, purpose: str) -> bool:
    """发送 5 分钟验证码，超时或临时调用失败按 Level1 规则重试一次。"""
    if not config.ALIYUN_ACCESS_KEY_ID or not config.ALIYUN_ACCESS_KEY_SECRET:
        logger.error("邮件服务不可用：error_type=MissingAccessKey email_len=%s", len(to_email))
        raise EmailServiceUnavailableError("邮件发送服务暂不可用")
    if not config.ALIYUN_MAIL_REGION_ID:
        logger.error("邮件服务不可用：error_type=MissingRegion email_len=%s", len(to_email))
        raise EmailServiceUnavailableError("邮件发送服务暂不可用")

    for attempt in range(2):
        try:
            _send_once(to_email, code, purpose)
            logger.info("验证码邮件发送成功：purpose=%s email_len=%s", purpose, len(to_email))
            return True
        except EmailServiceUnavailableError:
            raise
        except Exception as exc:
            retryable = _is_retryable_timeout(exc)
            logger.warning(
                "验证码邮件发送失败：purpose=%s email_len=%s attempt=%s error_type=%s",
                purpose,
                len(to_email),
                attempt + 1,
                type(exc).__name__,
            )
            if attempt == 0 and retryable:
                time.sleep(0.75)
                continue
            return False
    return False
