"""会话过期必须「看得见」。

背景（实测漏了 3 单自动发货）：闲鱼会话过期后，IM 长连接往往还活着（用的还是
旧 token），于是

  界面显示绿灯「监听中」、消息照收、卡密照发，
  只有 mtop 接口（确认发货 / 拉订单 / 图片上传 / 取 IM token）在静默失败。

而且「收到消息后冷却」会把会话检查无限期推迟 —— 越活跃的账号反而越查不出来。
这个文件守住三件事：

1. `_mark_session_expired` 能把账号标成「需重新扫码」且只通知一次；
2. 冷却不能无限期挡住会话检查（超过上限就放行）；
3. 确认发货接口返回会话过期时不再无意义重试，直接标记。
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from XianyuAutoAsync import XianyuLive
from app.secure_confirm import SecureConfirm


def _live(**attrs):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = '2222222222'
    live.needs_relogin = False
    live.relogin_reason = ''
    live.send_token_refresh_notification = AsyncMock()
    live.message_cookie_refresh_cooldown = 300
    live.session_check_max_delay = 900
    live.last_message_received_time = 0
    for k, v in attrs.items():
        setattr(live, k, v)
    return live


class MarkSessionExpiredTests(unittest.IsolatedAsyncioTestCase):
    async def test_sets_flag_and_reason_and_notifies_once(self):
        live = _live()
        first = await live._mark_session_expired('登录态已过期')
        self.assertTrue(first)
        self.assertTrue(live.needs_relogin)
        self.assertEqual(live.relogin_reason, '登录态已过期')
        self.assertEqual(live.send_token_refresh_notification.await_count, 1)

        # 再次标记不该重复通知（避免刷屏）
        second = await live._mark_session_expired('登录态已过期')
        self.assertFalse(second)
        self.assertTrue(live.needs_relogin)
        self.assertEqual(live.send_token_refresh_notification.await_count, 1)

    async def test_notification_failure_does_not_raise(self):
        live = _live()
        live.send_token_refresh_notification = AsyncMock(side_effect=RuntimeError('boom'))
        self.assertTrue(await live._mark_session_expired('登录态已过期'))
        self.assertTrue(live.needs_relogin)


class CooldownHardCapTests(unittest.TestCase):
    """冷却不能无限期推迟会话检查。"""

    INTERVAL = 1200

    def _blocks(self, last_check_ago, last_msg_ago):
        now = 100000.0
        live = _live(
            last_message_received_time=now - last_msg_ago,
            session_check_max_delay=900,
        )
        return live._cooldown_blocks_session_check(
            now, now - last_check_ago, self.INTERVAL
        )

    def test_no_message_yet_never_blocks(self):
        now = 100000.0
        live = _live(last_message_received_time=0)
        self.assertFalse(
            live._cooldown_blocks_session_check(now, now - 99999, self.INTERVAL)
        )

    def test_message_outside_cooldown_does_not_block(self):
        self.assertFalse(self._blocks(last_check_ago=1200, last_msg_ago=400))

    def test_recent_message_blocks_when_not_yet_overdue(self):
        # 刚过检查点、且 10 秒前有消息 → 按冷却推迟
        self.assertTrue(self._blocks(last_check_ago=1200, last_msg_ago=10))

    def test_recent_message_no_longer_blocks_after_hard_cap(self):
        # 一直有消息，但已经推迟超过 interval + 900 → 必须放行，否则永远查不出会话过期
        self.assertFalse(self._blocks(last_check_ago=1200 + 901, last_msg_ago=10))
        # 刚好到上限边界：仍然推迟
        self.assertTrue(self._blocks(last_check_ago=1200 + 899, last_msg_ago=10))


class NormalTokenExpiryFilterTests(unittest.TestCase):
    """会话过期必须发通知；可自愈的令牌过期才该静默。

    回归：_is_normal_token_expiry 曾经把 'Session过期' / 'FAIL_SYS_SESSION_EXPIRED'
    也当成「正常」吞掉 —— 方向刚好反了。会话过期是终态（只能重新扫码），
    而且此时 IM 长连接往往还活着、界面还是绿灯，不通知用户就完全无办法知道。
    实测因为这条，账号死了几小时、漏了 3 单自动发货，一条通知都没出去。
    """

    def setUp(self):
        self.live = XianyuLive.__new__(XianyuLive)

    def test_session_expired_is_not_treated_as_normal(self):
        for message in (
            'FAIL_SYS_SESSION_EXPIRED::Session过期',
            '检测到Session过期，但未配置用户名或密码，无法自动刷新Cookie',
            '登录态已过期且无法自动续期',
            '确认发货接口返回会话过期（订单 1）',
        ):
            with self.subTest(message=message):
                self.assertFalse(self.live._is_normal_token_expiry(message))

    def test_self_healing_token_expiry_stays_silent(self):
        for message in (
            'FAIL_SYS_TOKEN_EXOIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXPIRED::令牌过期',
            'Token定时刷新失败，将自动重试',
        ):
            with self.subTest(message=message):
                self.assertTrue(self.live._is_normal_token_expiry(message))


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return _FakeResp(self.payload)


class AutoConfirmSessionExpiredTests(unittest.IsolatedAsyncioTestCase):
    """确认发货遇到会话过期：不重试，直接标记账号需重新登录。"""

    async def _run(self, payload):
        session = _FakeSession(payload)
        main = _live()
        confirm = SecureConfirm(session, 'cookie2=x; _m_h5_tk=tok_1', '2222222222', main)
        with patch('app.secure_confirm.asyncio.sleep', AsyncMock()):
            result = await confirm.auto_confirm('3317077920214000476')
        return session, main, result

    async def test_session_expired_does_not_retry_and_marks(self):
        session, main, result = await self._run(
            {'ret': ['FAIL_SYS_SESSION_EXPIRED::Session过期']}
        )
        self.assertEqual(session.calls, 1)          # 以前会递归 4 次
        self.assertFalse(result.get('success'))
        self.assertTrue(result.get('need_relogin'))
        self.assertTrue(main.needs_relogin)
        self.assertIn('确认发货', main.relogin_reason)

    async def test_other_error_still_retries(self):
        session, main, result = await self._run({'ret': ['FAIL_SYS_BIZ_ERROR::其他错误']})
        self.assertGreater(session.calls, 1)        # 普通错误仍然重试
        self.assertFalse(main.needs_relogin)


if __name__ == '__main__':
    unittest.main()
