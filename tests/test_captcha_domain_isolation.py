# -*- coding: utf-8 -*-
"""回归：x5sec 必须区分域名（goofish vs taobao）。

2026-09-30 事故：用户每次手动过完滑块都跳转到 taobao.com，然后验证码窗口
又不断弹出，账号始终不恢复。根因是 open_manual_session 用
`_from_playwright_cookies(await context.cookies())` **不分域**地抓 cookie：
滑块通过后惩罚页 302 到 taobao.com，那里也下发一个同名 x5sec
（内容是 aserver/device 令牌，实测解开是 `{"s;2":...,"aserver;3":"0|...账号ID..."}`），
被当成 goofish 的挑战凭证存回账号 → goofish 不认 →
token 刷新继续 FAIL_SYS_USER_VALIDATE → 又弹一次滑块，无限循环。
"""

import unittest

from utils.manual_captcha import (
    _from_cookie_snapshot,
    _is_goofish_cookie,
    _x5sec_values,
    _has_new_x5sec,
)


class GoofishDomainFilterTests(unittest.TestCase):
    def test_goofish_domains_accepted(self):
        self.assertTrue(_is_goofish_cookie({'name': 'x5sec', 'domain': '.goofish.com'}))
        self.assertTrue(_is_goofish_cookie({'name': 'x5sec', 'domain': 'goofish.com'}))
        self.assertTrue(
            _is_goofish_cookie({'name': 'x5sec', 'domain': 'h5api.m.goofish.com'})
        )

    def test_taobao_rejected(self):
        for d in ('.taobao.com', 'taobao.com', 'h5api.m.taobao.com', '.tmall.com'):
            self.assertFalse(
                _is_goofish_cookie({'name': 'x5sec', 'domain': d}),
                f'{d} 不应被当成 goofish 域',
            )

    def test_blank_domain_is_lenient(self):
        self.assertTrue(_is_goofish_cookie({'name': 'x5sec'}))


class X5SecDomainIsolationTests(unittest.TestCase):
    def test_domain_only_filters_out_taobao(self):
        ck = [
            {'name': 'x5sec', 'value': 'GOOFISH', 'domain': '.goofish.com'},
            {'name': 'x5sec', 'value': 'TAOBAO', 'domain': '.taobao.com'},
        ]
        self.assertEqual(_x5sec_values(ck, domain_only=True), {'GOOFISH'})
        self.assertEqual(_x5sec_values(ck, domain_only=False), {'GOOFISH', 'TAOBAO'})

    def test_taobao_only_new_value_is_not_success(self):
        """这正是导致“无限弹窗”的误判，必须有测试守住。"""
        injected = [{'name': 'x5sec', 'value': 'OLD', 'domain': '.goofish.com'}]
        baseline = _x5sec_values(injected, domain_only=True)
        polluted = injected + [
            {'name': 'x5sec', 'value': 'TAOBAO_NEW', 'domain': '.taobao.com'}
        ]
        self.assertFalse(_has_new_x5sec(polluted, baseline, domain_only=True))

    def test_real_goofish_new_value_is_success(self):
        injected = [{'name': 'x5sec', 'value': 'OLD', 'domain': '.goofish.com'}]
        baseline = _x5sec_values(injected, domain_only=True)
        real = injected + [
            {'name': 'x5sec', 'value': 'NEW', 'domain': '.goofish.com'}
        ]
        self.assertTrue(_has_new_x5sec(real, baseline, domain_only=True))


class SnapshotTests(unittest.TestCase):
    def test_snapshot_roundtrip(self):
        self.assertEqual(_from_cookie_snapshot({'a': '1', 'b': '2'}), 'a=1; b=2')

    def test_empty_snapshot(self):
        self.assertEqual(_from_cookie_snapshot({}), '')
        self.assertEqual(_from_cookie_snapshot(None), '')


class SourceGuardsTests(unittest.TestCase):
    """守住实现细节：不能再出现不分域的 context.cookies() 直接拼串。"""

    def test_no_unfiltered_cookie_harvest(self):
        import pathlib
        import re

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        # 不允许 `_from_playwright_cookies(await context.cookies())` 这种写法
        bad = re.findall(
            r'_from_playwright_cookies\(\s*await\s+context\.cookies\(\)\s*\)', src
        )
        self.assertEqual(bad, [], '存在不分域取 cookie 的写法，会抓到 taobao 的 x5sec')

    def test_uses_goofish_snapshot(self):
        import pathlib

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        self.assertIn('_goofish_cookies_snapshot', src)
        self.assertIn('domain_only=True', src)


if __name__ == '__main__':
    unittest.main()
