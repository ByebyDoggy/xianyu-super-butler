# -*- coding: utf-8 -*-
"""通知类别统一管理。

2026-10-01 需求：用户希望通知能精确区分「买家发来消息」和「系统触发的事件
（风控弹验证码、Token 异常等）」，避免收到通知时还要点开才能分辨。

设计：
- 每个类别一个稳定的 key（存库/日志用）+ 展示用图标和标题。
- send_notification / send_token_refresh_notification 接受类别 key 或
  NotificationCategory 枚举；未知 key 回落到 user_message / token_error。
- notification_type（send_token_refresh_notification 的既有参数）通过
  category_from_notification_type 自动映射，老调用点无需逐个改参数。
"""
from __future__ import annotations

from enum import Enum


class NotificationCategory(Enum):
    """通知类别。

    user_message   买家发来的聊天消息（最需要及时看到）
    risk_captcha   风控事件：滑块验证、人工验证提醒、验证未完成
    token_error    Token/Cookie 异常：刷新失败、需要重新登录、更新失败
    account_status 账号状态变更（好消息）：滑块通过并恢复、实例重启等
    test           测试通知（后台「测试通知」按钮）
    """

    USER_MESSAGE = "user_message"
    RISK_CAPTCHA = "risk_captcha"
    TOKEN_ERROR = "token_error"
    ACCOUNT_STATUS = "account_status"
    TEST = "test"

    @property
    def icon(self) -> str:
        return _CATEGORY_META[self].icon

    @property
    def title(self) -> str:
        return _CATEGORY_META[self].title


class _Meta:
    __slots__ = ("icon", "title")

    def __init__(self, icon: str, title: str):
        self.icon = icon
        self.title = title


_CATEGORY_META = {
    NotificationCategory.USER_MESSAGE: _Meta("💬", "买家消息通知"),
    NotificationCategory.RISK_CAPTCHA: _Meta("⚠️", "风控验证通知"),
    NotificationCategory.TOKEN_ERROR: _Meta("🔑", "账号异常通知"),
    NotificationCategory.ACCOUNT_STATUS: _Meta("✅", "账号状态通知"),
    NotificationCategory.TEST: _Meta("🧪", "测试通知"),
}


def normalize_category(category) -> NotificationCategory:
    """把任意输入（枚举/字符串/None）安全转成枚举，未知值回落买家消息。"""
    if isinstance(category, NotificationCategory):
        return category
    if isinstance(category, str):
        try:
            return NotificationCategory(category)
        except ValueError:
            pass
    return NotificationCategory.USER_MESSAGE


# notification_type（历史参数）→ 通知类别 的映射。
# 规则：captcha_* 开头 → 风控；token_* / cookie_* / need_relogin / db_* /
# face_verification → 账号异常；captcha_success* / instance_* → 状态变更；
# notification_test → 测试；其余 → 账号异常（Token 通道默认是异常告警）。
_TYPE_RULES = (
    ("notification_test", NotificationCategory.TEST),
    ("captcha_success", NotificationCategory.ACCOUNT_STATUS),
    ("instance_restart_failed", NotificationCategory.TOKEN_ERROR),
    ("instance_restart", NotificationCategory.ACCOUNT_STATUS),
    ("captcha", NotificationCategory.RISK_CAPTCHA),
    ("face_verification", NotificationCategory.RISK_CAPTCHA),
    ("need_relogin", NotificationCategory.TOKEN_ERROR),
    ("token", NotificationCategory.TOKEN_ERROR),
    ("cookie", NotificationCategory.TOKEN_ERROR),
    ("db_update_failed", NotificationCategory.TOKEN_ERROR),
    ("cookie_id_missing", NotificationCategory.TOKEN_ERROR),
)


def category_from_notification_type(notification_type: str) -> NotificationCategory:
    """按 send_token_refresh_notification 的 notification_type 推断类别。"""
    nt = (notification_type or "").lower()
    for prefix, cat in _TYPE_RULES:
        if nt.startswith(prefix) or prefix in nt:
            return cat
    return NotificationCategory.TOKEN_ERROR


def format_header(category) -> str:
    """生成通知文本的首行标题，如「💬 买家消息通知」/「⚠️ 风控验证通知」。"""
    cat = normalize_category(category)
    return f"{cat.icon} {cat.title}"
