"""回归：人工滑块「完成」必须以 x5sec 为准。

2026-09-29 事故：check_completion 只看「滑块元素不可见」，于是惩罚页抖动
就被判为完成，拿回一份只带 x5secdata、没有 x5sec 的 Cookie 去覆盖账号
Cookie —— 下一轮接口照样 FAIL_SYS_USER_VALIDATE，表现为「滑块过了但账号
一直不恢复」，而且日志还写着"人工验证完成"，极具误导性。
"""

import unittest

from utils.manual_captcha import _has_x5sec


class HasX5SecTests(unittest.TestCase):
    def test_requires_real_x5sec(self):
        self.assertTrue(_has_x5sec([{'name': 'x5sec', 'value': 'v'}]))
        self.assertTrue(_has_x5sec([{'name': 'X5SEC', 'value': 'v'}]))

    def test_challenge_markers_are_not_success(self):
        # x5secdata / x5sectag 是「正在被挑战」的标记，恰恰说明还没过
        self.assertFalse(_has_x5sec([{'name': 'x5secdata', 'value': 'v'}]))
        self.assertFalse(_has_x5sec([{'name': 'x5sectag', 'value': 'v'}]))

    def test_empty_and_junk(self):
        self.assertFalse(_has_x5sec([]))
        self.assertFalse(_has_x5sec(None))
        self.assertFalse(_has_x5sec([{'name': 'unb', 'value': 'v'}]))


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
