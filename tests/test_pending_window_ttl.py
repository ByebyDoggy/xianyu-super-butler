# -*- coding: utf-8 -*-
"""验证窗口登记残留检测：TTL 兜底 + 键名双清。"""
import unittest
import time
from unittest.mock import patch


class PendingWindowTTLTests(unittest.TestCase):
    def test_fresh_entry_visible(self):
        from utils.browser_login import (
            _PENDING_VERIFICATION_WINDOWS, pending_verification_window,
            clear_pending_verification_window)
        key = f'test_{time.time()}'
        _PENDING_VERIFICATION_WINDOWS[key] = time.time()
        try:
            self.assertIsNotNone(pending_verification_window(key))
        finally:
            clear_pending_verification_window(key)

    def test_stale_entry_auto_invalidated(self):
        """超过 20 分钟的登记自动作废 —— 用户看到的「4350 秒前」就是这种残留。"""
        from utils.browser_login import (
            _PENDING_VERIFICATION_WINDOWS, pending_verification_window,
            clear_pending_verification_window, PENDING_VERIFICATION_TTL)
        key = 'stale_test'
        _PENDING_VERIFICATION_WINDOWS[key] = time.time() - (PENDING_VERIFICATION_TTL + 10)
        try:
            self.assertIsNone(pending_verification_window(key))
            # 自动清除
            self.assertNotIn(key, _PENDING_VERIFICATION_WINDOWS)
        finally:
            _PENDING_VERIFICATION_WINDOWS.pop(key, None)

    def test_reply_server_clears_both_keys(self):
        import pathlib
        src = pathlib.Path('app/reply_server.py').read_text(encoding='utf-8')
        # finally 里必须同时清 target 和 '新账号'
        self.assertIn("clear_pending_verification_window(target or '新账号')", src)
        self.assertIn("clear_pending_verification_window('新账号')", src)

    def test_ttl_registered(self):
        from utils.browser_login import PENDING_VERIFICATION_TTL
        self.assertEqual(PENDING_VERIFICATION_TTL, 1200)


if __name__ == '__main__':
    unittest.main()
