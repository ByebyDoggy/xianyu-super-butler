"""人工验证窗口的「唯一持有者」占位锁。

## 为什么需要它

同一个账号的滑块验证，历史上会从**三条互不知情的路径**同时发起：

1. ``/api/browser-login``（用户点“浏览器登录”）→ 保留窗口后跑人工验证
2. ``refresh_cookies_from_qr_login``（扫码 Cookie 后台增强）→ 也要用窗口
3. ``_auto_open_slider_browser``（后台 Token 刷新命中风控）→ 还会再开一个

它们都在**同一个进程**里，所以基于文件的 profile 锁拦不住「复用同一个
上下文」这条路 —— 复用路径不经过 ``acquire_profile_async``。
实测后果（2026-09-29 17:37）：

    17:37:14  复用已打开的有头浏览器窗口进行人工验证   ← 会话 A
    17:37:16  自动弹出本项目有头浏览器                  ← 会话 B 另开一个
    17:37:20  复用已打开的有头浏览器窗口进行人工验证   ← B 也复用同一窗口
    17:37:21  已开启人工验证会话                       ← A
    17:37:23  已开启人工验证会话                       ← B（同一窗口两个会话）
    17:37:38  同窗口人工验证未完成: 已导航到惩罚页但未检测到滑块

两个会话在同一个 page 上互相导航、互相打断，滑块永远出不来；用户看到的是
“突然蹦出来两个浏览器”“原窗口的滑块没通过就关了”。

## 用法

验证开始前 ``claim()``，拿到 False 就**不要**动窗口（已经有别人在做）；
结束时用 ``release()`` 归还。``holder()`` 用于给用户/日志一个可读的说明。
"""

import time
from typing import Dict, Optional

_owners: Dict[str, tuple] = {}  # {cookie_id: (owner, claimed_at)}


def claim(cookie_id: str, owner: str) -> bool:
    """尝试成为该账号人工验证的唯一持有者。

    Returns:
        True  = 抢到了，可以动窗口/开浏览器。
        False = 已有别的流程在做，调用方应直接放弃而不是再开一个窗口。
    """
    key = str(cookie_id or '')
    existing = _owners.get(key)
    if existing is not None:
        return False
    _owners[key] = (owner, time.time())
    return True


def release(cookie_id: str, owner: Optional[str] = None) -> None:
    """归还持有权。

    owner 给定时只在持有者匹配时归还，避免 A 把 B 的占位误删。
    """
    key = str(cookie_id or '')
    existing = _owners.get(key)
    if existing is None:
        return
    if owner is not None and existing[0] != owner:
        return
    _owners.pop(key, None)


def holder(cookie_id: str) -> Optional[str]:
    """当前持有者名字（None = 没人持有）。"""
    existing = _owners.get(str(cookie_id or ''))
    return existing[0] if existing else None


def held_seconds(cookie_id: str) -> float:
    """已被持有多少秒（没人持有返回 0）。"""
    existing = _owners.get(str(cookie_id or ''))
    return (time.time() - existing[1]) if existing else 0.0


def describe(cookie_id: str) -> str:
    """给用户看的一句话说明。"""
    existing = _owners.get(str(cookie_id or ''))
    if existing is None:
        return ''
    return f'{existing[0]}（已持有 {int(time.time() - existing[1])} 秒）'
