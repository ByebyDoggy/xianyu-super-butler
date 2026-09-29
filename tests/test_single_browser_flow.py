"""回归：扫码登录 → 验证码 → 过滑块 必须全程同一窗口。

2026-09-29 用户反馈：「完成扫码登录后会触发一个验证码，但此瞬间只有不足
2 秒，之后窗口被立即关闭了，然后又出来一个浏览器是滑块」，要求「全部流程
都应该在一个有头浏览器内进行」。

原因：三个独立流程各开各的窗口，且看到 unb 就无条件 close：
  1. utils.browser_login.open_login_session  —— 登录成功即关（<2 秒）
  2. XianyuAutoAsync.refresh_cookies_from_qr_login —— 取完 Cookie 就关
  3. XianyuAutoAsync._auto_open_slider_browser —— 再开一个过滑块
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from utils import browser_login


class LoginWindowHandoffTests(unittest.TestCase):
    """登录窗口检测到验证码时必须保留，而不是关掉再新开。"""

    def test_captcha_pending_detects_punish_url(self):
        page = MagicMock()
        page.url = 'https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc/punish?x5secdata=abc'
        page.frames = []

        async def run():
            return await browser_login._captcha_pending(None, page)

        self.assertTrue(asyncio.run(run()))

    def test_captcha_pending_false_on_normal_page(self):
        page = MagicMock()
        page.url = 'https://www.goofish.com/'
        page.frames = []

        async def run():
            return await browser_login._captcha_pending(None, page)

        self.assertFalse(asyncio.run(run()))

    def test_captcha_pending_detects_slider_element(self):
        element = MagicMock()
        element.is_visible = AsyncMock(return_value=True)
        frame = MagicMock()
        frame.query_selector = AsyncMock(return_value=element)
        page = MagicMock()
        page.url = 'https://www.goofish.com/'
        page.frames = [frame]

        async def run():
            return await browser_login._captcha_pending(None, page)

        self.assertTrue(asyncio.run(run()))


class LoginSignatureTests(unittest.TestCase):
    def test_open_login_session_supports_keep_open(self):
        import inspect

        sig = inspect.signature(browser_login.open_login_session)
        self.assertIn('keep_open_if_captcha', sig.parameters)


class ManualSessionReuseTests(unittest.TestCase):
    def test_open_manual_session_supports_reuse_context(self):
        import inspect

        from utils.manual_captcha import open_manual_session

        sig = inspect.signature(open_manual_session)
        self.assertIn('reuse_context', sig.parameters)


class NoHardcodedImPageTests(unittest.TestCase):
    """取 Cookie 不得再访问 /im（会顶掉机器人 IM 长连接）。"""

    def test_no_im_url_in_production_paths(self):
        import pathlib
        import re

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        # 允许出现在注释里作为反面说明，但不允许作为赋值/参数使用
        offenders = [
            line for line in src.splitlines()
            if 'goofish.com/im' in line and not line.lstrip().startswith('#')
        ]
        self.assertEqual(offenders, [], f'发现硬编码 /im: {offenders}')


class HandoffFlagTests(unittest.TestCase):
    """交给人工验证后，finally 不得再关闭那个窗口。"""

    def test_qr_flow_has_handoff_flag(self):
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        self.assertIn('handed_off_to_manual', src)
        self.assertIn('本流程不关闭它', src)

    def test_browser_login_guards_close_with_keep_open(self):
        import pathlib

        src = pathlib.Path('utils/browser_login.py').read_text(encoding='utf-8')
        self.assertIn("result.get('keep_open')", src)
        # finally 里 return 会吞掉异常，必须避免
        self.assertNotIn('登录窗口已保留给人工验证，本流程不关闭它\'\n            return', src)


if __name__ == '__main__':
    unittest.main()


class NoSilentProfileHangTests(unittest.TestCase):
    """回归：不能因为窗口占着 profile 就让用户“点了没反应”。

    2026-09-29 实测：保留的验证窗口占着 profile，后续两次点击各卡 300 秒
    且日志里毫无输出，用户完全不知道发生了什么。
    """

    def test_pending_window_short_circuits_with_message(self):
        import time

        from utils import browser_login as B

        label = '新账号'
        B._PENDING_VERIFICATION_WINDOWS[label] = time.time() - 30
        try:
            info = asyncio.run(B.open_login_session(cookie_id=None, timeout=10))
        finally:
            B._PENDING_VERIFICATION_WINDOWS.pop(label, None)
        self.assertFalse(info['success'])
        self.assertIn('已有一个验证码窗口打开着', info['message'])
        self.assertIn('30 秒前', info['message'])

    def test_no_pending_window_proceeds(self):
        from utils import browser_login as B

        self.assertIsNone(B.pending_verification_window('不存在的账号'))

    def test_acquire_profile_supports_short_timeout(self):
        import inspect

        from utils.browser_profile import acquire_profile_async

        sig = inspect.signature(acquire_profile_async)
        self.assertIn('timeout', sig.parameters)
        self.assertIsNone(sig.parameters['timeout'].default)

    def test_login_uses_short_lock_timeout(self):
        import pathlib

        src = pathlib.Path('utils/browser_login.py').read_text(encoding='utf-8')
        self.assertIn('本地浏览器登录\', timeout=', src)
