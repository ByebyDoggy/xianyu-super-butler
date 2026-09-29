"""本地浏览器登录：光有 unb 不算登录成功。

实测故障：点「本地浏览器登录」后窗口刚弹出来就自己关掉了，浏览器根本没登录，
系统却回报「Cookie 已更新」。

原因：登录成功的判定只有一句 `_find_unb(cookies)` —— 而浏览器用的是**持久化
共享 profile**，里面可能残留上一次的旧会话（unb / cookie2 都在，服务端已经不认）。
于是第一次轮询就"检测到登录成功"，直接取走同一份失效 Cookie 收工。

修法：检测到 unb 之后必须拿实际接口验一次登录态真的能用。
这个文件守住判定规则本身。
"""

import unittest

from utils.browser_login import _session_usable_from_token_response


class SessionUsableClassificationTests(unittest.TestCase):
    def test_success_is_usable(self):
        self.assertTrue(_session_usable_from_token_response(
            {'ret': ['SUCCESS::调用成功'], 'data': {'accessToken': 'x'}}
        ))

    def test_risk_control_means_session_is_valid(self):
        """需要过滑块 = 会话有效，只是要验证；不能判成「没登录」。"""
        for payload in (
            {'ret': ['FAIL_SYS_USER_VALIDATE', 'RGV587_ERROR::哎哟喂,被挤爆啦']},
            {'ret': [], 'data': {'url': 'https://h5api.m.goofish.com/punish?action=captcha&x5secdata=x'}},
        ):
            with self.subTest(payload=payload):
                self.assertTrue(_session_usable_from_token_response(payload))

    def test_session_expired_is_not_usable(self):
        self.assertFalse(_session_usable_from_token_response(
            {'ret': ['FAIL_SYS_SESSION_EXPIRED::Session过期'], 'data': {}}
        ))

    def test_old_session_fingerprints_are_not_usable(self):
        """只有 unb/cookie2 但没有有效令牌 → 旧会话，不算登录成功。"""
        for payload in (
            {'ret': ['FAIL_SYS_TOKEN_EMPTY::令牌为空']},
            {'ret': ['FAIL_SYS_ILLEGAL_ACCESS::非法请求']},
            {'ret': [], 'data': {}},
            {},
        ):
            with self.subTest(payload=payload):
                self.assertFalse(_session_usable_from_token_response(payload))


if __name__ == '__main__':
    unittest.main()
