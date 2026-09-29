"""回归：人工滑块「完成」必须以 x5sec 为准。

2026-09-29 事故：check_completion 只看「滑块元素不可见」，于是惩罚页抖动
就被判为完成，拿回一份只带 x5secdata、没有 x5sec 的 Cookie 去覆盖账号
Cookie —— 下一轮接口照样 FAIL_SYS_USER_VALIDATE，表现为「滑块过了但账号
一直不恢复」，而且日志还写着"人工验证完成"，极具误导性。
"""

import unittest

from utils.manual_captcha import _has_new_x5sec, _x5sec_values


class HasNewX5SecTests(unittest.TestCase):
    """判据必须是「服务端新发了 x5sec」，不是「Cookie 里有 x5sec」。

    2026-09-29 15:50 实测：账号 Cookie 带着上一次通过留下的旧 x5sec，注入
    浏览器后第一次轮询就命中，会话开启 2 秒即宣告完成、窗口关闭，每 8 秒
    循环弹一次，用户根本没机会拖滑块。
    """

    def test_stale_x5sec_is_not_success(self):
        injected = [{'name': 'x5sec', 'value': 'old'}]
        self.assertFalse(_has_new_x5sec(injected, _x5sec_values(injected)))

    def test_newly_issued_x5sec_is_success(self):
        injected = [{'name': 'x5sec', 'value': 'old'}]
        base = _x5sec_values(injected)
        after = injected + [{'name': 'x5sec', 'value': 'brand-new'}]
        self.assertTrue(_has_new_x5sec(after, base))

    def test_replaced_value_is_success(self):
        self.assertTrue(
            _has_new_x5sec([{'name': 'x5sec', 'value': 'new'}], {'old'})
        )

    def test_case_insensitive_name(self):
        self.assertTrue(
            _has_new_x5sec([{'name': 'X5SEC', 'value': 'new'}], {'old'})
        )

    def test_no_x5sec_at_all(self):
        self.assertFalse(_has_new_x5sec([{'name': 'unb', 'value': '1'}], {'old'}))
        self.assertFalse(_has_new_x5sec([], {'old'}))
        self.assertFalse(_has_new_x5sec(None, {'old'}))

    def test_empty_baseline(self):
        self.assertTrue(_has_new_x5sec([{'name': 'x5sec', 'value': 'x'}], set()))

    def test_challenge_markers_alone_are_not_success(self):
        self.assertFalse(
            _has_new_x5sec(
                [{'name': 'x5secdata', 'value': 'v'}, {'name': 'x5sectag', 'value': 'v'}],
                set(),
            )
        )


class SavedCookieMustContainX5SecTests(unittest.TestCase):
    def test_drop_stale_keeps_markers_when_no_x5sec(self):
        """没有 x5sec 时不得清理挑战标记（标记还要用于定位惩罚页）。"""
        from utils.xianyu_utils import drop_stale_captcha_challenge

        raw = "unb=1; x5secdata=abc; x5sectag=def"
        self.assertEqual(drop_stale_captcha_challenge(raw), raw)

    def test_drop_stale_clears_markers_once_x5sec_present(self):
        from utils.xianyu_utils import drop_stale_captcha_challenge

        out = drop_stale_captcha_challenge("unb=1; x5sec=ok; x5secdata=abc")
        self.assertIn('x5sec=ok', out)
        self.assertNotIn('x5secdata', out)


if __name__ == '__main__':
    unittest.main()
