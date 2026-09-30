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


class CaptchaPopupBackoffTests(unittest.TestCase):
    """回归：验证没完成时不能无限弹窗。

    2026-09-30 实测：弹窗等 600 秒超时 → 立刻重弹，每 12 分钟一轮，
    用户「永远关不完窗口」。必须逐次拉长间隔并在上限后停止。
    """

    def setUp(self):
        import XianyuAutoAsync as m

        self.m = m
        m._clear_captcha_open_streak('acct')

    def tearDown(self):
        self.m._clear_captcha_open_streak('acct')

    def test_backoff_escalates(self):
        m = self.m
        self.assertEqual(m._captcha_open_cooldown('acct'), 0.0)
        m._note_captcha_open('acct')
        self.assertEqual(m._captcha_open_cooldown('acct'), 20.0)
        m._note_captcha_open('acct')
        self.assertEqual(m._captcha_open_cooldown('acct'), 300.0)
        m._note_captcha_open('acct')
        self.assertEqual(m._captcha_open_cooldown('acct'), 1800.0)

    def test_streak_caps_out(self):
        m = self.m
        for _ in range(m.CAPTCHA_AUTO_OPEN_ATTEMPTS):
            m._note_captcha_open('acct')
        self.assertGreaterEqual(
            m._CAPTCHA_AUTO_OPEN_STREAK['acct'], m.CAPTCHA_AUTO_OPEN_ATTEMPTS
        )

    def test_success_clears_streak(self):
        m = self.m
        m._note_captcha_open('acct')
        m._clear_captcha_open_streak('acct')
        self.assertNotIn('acct', m._CAPTCHA_AUTO_OPEN_STREAK)
        self.assertEqual(m._captcha_open_cooldown('acct'), 0.0)


class PasteCookieResetsRiskTests(unittest.TestCase):
    """回归：粘贴有效 Cookie 必须重置风控并触发重连。

    2026-09-30 实测：粘贴后 Cookie 确实是好的（含 x5sec、无 x5secdata），
    但界面依然显示风控中、浏览器窗口继续弹 —— 因为粘贴接口只保存 Cookie
    + 重启任务，从没碰过风控状态。
    """

    def test_paste_endpoint_resets_risk_control(self):
        import pathlib

        src = pathlib.Path('app/reply_server.py').read_text(encoding='utf-8')
        # 找到粘贴 Cookie 那段
        idx = src.find('粘贴Cookie保存完成')
        self.assertGreater(idx, 0)
        window = src[max(0, idx - 4000): idx + 4000]
        self.assertIn('guard.reset()', window, '粘贴 Cookie 后必须重置风控状态')
        self.assertIn('request_immediate_reconnect', window, '粘贴后必须触发立即重连')
        self.assertIn('_clear_captcha_open_streak', window, '粘贴后必须清零弹窗计数')


class SnapshotDedupTests(unittest.TestCase):
    """回归：同名 Cookie 跨域共存时，快照必须留下「域更具体」的那份。

    2026-09-30 实测事故：注入的账号 Cookie 挂在 .goofish.com，惩罚页
    新下发的 x5sec 挂在 h5api.m.goofish.com（host-only），两份同名 x5sec
    共存。旧快照用 {name: value} 字典直存，谁留下全看遍历顺序 —— 结果
    检测到了新 x5sec（日志有「检测到新的 goofish x5sec」），落库的却仍是
    注入的**旧**值（与 03:17 粘贴的完全相同），新通行证被静默丢弃，
    平台照样拒绝，滑块窗口无限弹。
    """

    OLD_INJECTED = {'name': 'x5sec', 'value': 'OLD_P', 'domain': '.goofish.com'}
    NEW_HOSTONLY = {'name': 'x5sec', 'value': 'NEW_Z', 'domain': 'h5api.m.goofish.com'}

    def test_dedup_prefers_more_specific_domain(self):
        from utils.manual_captcha import _dedup_goofish

        for order in ([self.OLD_INJECTED, self.NEW_HOSTONLY],
                      [self.NEW_HOSTONLY, self.OLD_INJECTED]):
            snap = _dedup_goofish(order + [
                {'name': 'unb', 'value': '123', 'domain': '.goofish.com'},
            ])
            self.assertEqual(snap['x5sec'], 'NEW_Z', f'顺序 {order[0]["value"]} 在前也不行')
            self.assertEqual(snap['unb'], '123')

    def test_dedup_ignores_taobao(self):
        from utils.manual_captcha import _dedup_goofish

        snap = _dedup_goofish([
            self.OLD_INJECTED,
            {'name': 'x5sec', 'value': 'TAOBAO', 'domain': '.taobao.com'},
        ])
        self.assertEqual(snap['x5sec'], 'OLD_P')
        self.assertNotIn('TAOBAO', snap.values())

    def test_prefer_overrides_snapshot(self):
        from utils.manual_captcha import _goofish_cookies_snapshot

        class FakeCtx:
            async def cookies(self):
                # 故意让旧值排在后面（旧 bug 场景：后写覆盖先写）
                return [self.NEW_HOSTONLY, self.OLD_INJECTED] if False else [
                    {'name': 'x5sec', 'value': 'NEW_Z', 'domain': 'h5api.m.goofish.com'},
                    {'name': 'x5sec', 'value': 'OLD_P', 'domain': '.goofish.com'},
                ]

        import asyncio

        async def run():
            return await _goofish_cookies_snapshot(FakeCtx(), prefer={'x5sec': 'NEW_Z'})

        snap = asyncio.run(run())
        self.assertEqual(snap['x5sec'], 'NEW_Z')

    def test_x5sec_entries_and_pick_new(self):
        from utils.manual_captcha import _x5sec_entries, _pick_new_x5sec

        entries = _x5sec_entries(
            [
                {'name': 'x5sec', 'value': 'OLD_P', 'domain': '.goofish.com'},
                {'name': 'x5sec', 'value': 'NEW_Z', 'domain': 'h5api.m.goofish.com'},
                {'name': 'x5sec', 'value': 'T', 'domain': '.taobao.com'},
            ],
            domain_only=True,
        )
        self.assertEqual(entries, {'OLD_P': '.goofish.com', 'NEW_Z': 'h5api.m.goofish.com'})
        # 新值取域更具体的（host-only 的那份）
        self.assertEqual(_pick_new_x5sec(entries, {'OLD_P'}), 'NEW_Z')
        # 没有新值时返回空串
        self.assertEqual(_pick_new_x5sec(entries, {'OLD_P', 'NEW_Z'}), '')
        # 空入参
        self.assertEqual(_pick_new_x5sec({}, set()), '')


class ChallengeMarkerStripTests(unittest.TestCase):
    """回归：挑战标记（x5secdata/x5sectag/x5step）绝不入库、不随请求发送。

    2026-09-30 实测：FAIL_SYS_USER_VALIDATE 的响应通过 set-cookie 把标记
    塞回来，合并落库后，后续每个请求都带着「验证未完成」标记，平台继续
    拒绝 —— 滑块过了也不恢复、窗口无限弹。
    """

    def test_strip_string(self):
        from utils.xianyu_utils import strip_captcha_challenge_cookies

        s = 'unb=123; x5secdata=abc; cookie2=xyz; x5sectag=t; x5step=1'
        self.assertEqual(
            strip_captcha_challenge_cookies(s), 'unb=123; cookie2=xyz'
        )
        # 无标记原样
        self.assertEqual(strip_captcha_challenge_cookies('a=1; b=2'), 'a=1; b=2')
        # 空/None 安全
        self.assertEqual(strip_captcha_challenge_cookies(''), '')
        self.assertIsNone(strip_captcha_challenge_cookies(None))
        # x5sec 本体必须保留
        self.assertEqual(
            strip_captcha_challenge_cookies('x5sec=PASS; x5secdata=abc'),
            'x5sec=PASS',
        )

    def test_strip_dict_inplace(self):
        from utils.xianyu_utils import strip_challenge_markers_from_dict

        d = {'unb': '123', 'x5secdata': 'a', 'x5sectag': 'b', 'x5sec': 'PASS'}
        dropped = strip_challenge_markers_from_dict(d)
        self.assertEqual(sorted(dropped), ['x5secdata', 'x5sectag'])
        self.assertEqual(d, {'unb': '123', 'x5sec': 'PASS'})
        # 再跑一次应为空（幂等）
        self.assertEqual(strip_challenge_markers_from_dict(d), [])

    def test_update_config_cookies_strips_markers(self):
        """落库前必须清标记：update_config_cookies 是所有合并点的唯一漏斗。"""
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        idx = src.find('async def update_config_cookies')
        self.assertGreater(idx, 0)
        body = src[idx: idx + 2000]
        self.assertIn(
            'strip_challenge_markers_from_dict', body,
            'update_config_cookies 落库前必须清挑战标记',
        )

    def test_qr_harvest_strips_markers(self):
        """扫码登录采集的 Cookie 落库前也必须清标记。"""
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        needle = '】真实Cookie已获取，包含'
        found = 0
        pos = 0
        while True:
            idx = src.find(needle, pos)
            if idx < 0:
                break
            found += 1
            window = src[max(0, idx - 800): idx]
            self.assertIn(
                'strip_captcha_challenge_cookies', window,
                '扫码登录采集 Cookie 落库前必须清挑战标记',
            )
            pos = idx + 1
        self.assertGreaterEqual(found, 2, '应有两处扫码采集点都清挑战标记')
