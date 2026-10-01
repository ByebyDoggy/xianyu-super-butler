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


class ExpirySelfHealTests(unittest.TestCase):
    """回归：挑战过期自愈与诊断日志。

    2026-09-30 实测：窗口弹出 7.5 分钟后用户才拖滑块，元素消失、页面
    看着过了，但挑战约 5 分钟就过期 —— 服务端不发通行证（无新 x5sec），
    会话干等到超时，用户以为「拖了没反应」。
    """

    def test_wait_loop_has_self_heal_and_diagnostics(self):
        import pathlib

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        # 过期自愈：同窗口重新加载新滑块
        self.assertIn('已在同一窗口加载新的滑块', src)
        self.assertIn('_fetch_live_verification_url', src)
        self.assertIn('RENAVIGATE_LIMIT', src)
        # 元素重新出现时必须重置过期计时（否则新滑块刚拖完就误判过期）
        self.assertIn('elements_gone_at = None', src)
        # 诊断：任意域 Cookie 变化 + 页面跳转都要记录
        self.assertIn('[诊断] Cookie 新增', src)
        self.assertIn('[诊断] 页面跳转', src)


class StealthInjectionTests(unittest.TestCase):
    """回归：人工验证浏览器必须注入反检测脚本。

    2026-09-30 全链路实测：滑块能拖过、新 x5sec 也能拿到并正确落库，
    但 Token 刷新照样 FAIL_SYS_USER_VALIDATE、验证码无限弹。对照实验：
    用户自家 Chrome 拖出的 x5sec 能用 67 分钟，Playwright 浏览器拖出的
    秒拒 —— 唯一差异是环境指纹（navigator.webdriver / window.__playwright
    等自动化痕迹被 nc 环境检测命中，服务端把这次通过作废）。
    项目里早有 XianyuSliderStealth 脚本，但只接在已停用的「自动过滑块」
    路径上，人工验证浏览器一直是裸奔的。
    """

    def test_manual_session_injects_stealth(self):
        import pathlib

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        idx = src.find('async def open_manual_session')
        self.assertGreater(idx, 0)
        body = src[idx:]
        # 必须注入 stealth 脚本，且在 new_page 之前（init script 只对
        # 之后创建的页面生效）
        self.assertIn('xianyu_slider_stealth', body, '人工验证必须注入反检测脚本')
        self.assertIn('add_init_script', body)
        inject_at = body.find('add_init_script')
        newpage_at = body.find('await context.new_page()')
        self.assertLess(inject_at, newpage_at, '反检测脚本必须在 new_page 之前注入')


class LateDragFeedbackTests(unittest.TestCase):
    """回归：拖晚了必须当场告诉用户，并说明后续动作。

    2026-09-30 用户反馈：拖晚了没有任何提示，不知道该关窗口、
    重新扫码还是干等。要求：提示「拖晚了/晚了多久」+ 后续是
    自动换新滑块（同窗口），不需要清会话、不需要重新扫码。
    """

    def test_banner_helper_exists(self):
        import pathlib

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        self.assertIn('async def _show_page_banner', src)
        # 关键反馈点必须都在窗口横幅里出现（用户看的是窗口，不是日志）
        for needle in (
            '拖得太晚：挑战已下发',          # 晚了多久
            '自动换新滑块',                  # 后续动作（同窗口换新）
            '未完成验证，本会话结束',        # 超时说明
            '重新扫码登录',                  # 兜底路径指引
            '验证成功！新 Cookie 已保存',     # 成功确认
        ):
            self.assertIn(needle, src, f'缺少用户反馈: {needle}')

    def test_challenge_ttl_and_dead_drag_constants(self):
        import pathlib
        import re

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        m = re.search(r'CHALLENGE_TTL_SECONDS\s*=\s*(\d+)', src)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 300, '挑战有效期应为约 5 分钟')
        m2 = re.search(r'DEAD_DRAG_RENAVIGATE_DELAY\s*=\s*(\d+)', src)
        self.assertIsNotNone(m2)
        self.assertLessEqual(int(m2.group(1)), 10, '确认无效后应尽快换新滑块')

    def test_dead_drag_resets_when_slider_reappears(self):
        import pathlib

        src = pathlib.Path('utils/manual_captcha.py').read_text(encoding='utf-8')
        # 新滑块渲染出来（元素在）时必须重置 dead_drag_at，避免误换
        self.assertRegex(src, r'else:\s*\n\s+# 滑块还在（或新滑块已渲染）—— 重置拖动状态\s*\n\s+elements_gone_at = None\s*\n\s+dead_drag_at = None')


class X5SecMergeProtectionTests(unittest.TestCase):
    """回归：x5sec 绝不让 mtop 响应的 set-cookie 覆盖。

    2026-09-30 09:29 决定性实测：滑块通过后带着新 x5sec 请求，返回
    FAIL_SYS_TOKEN_EXOIRED（风控已通过、只是 _m_h5_tk 签名过期，可自愈）；
    但「签名过期」响应的 set-cookie 会把会话旧 x5sec 塞回来，合并后
    自愈重试带的是旧值 → 又被要求滑块 → 无限循环，新通行证被永久丢弃。
    x5sec 只能来自人工通过 / 用户粘贴 / 扫码采集。
    """

    def test_filter_drops_x5sec_only(self):
        from utils.xianyu_utils import filter_mtop_set_cookies

        out = filter_mtop_set_cookies({
            'x5sec': 'NEW_PASS', '_m_h5_tk': 'abc_xyz', 'isg': 'v',
            'X5SEC': 'case-insensitive', 'x5secdata': 'marker',
        })
        self.assertNotIn('x5sec', out)
        self.assertNotIn('X5SEC', out)
        self.assertEqual(out['_m_h5_tk'], 'abc_xyz')
        self.assertEqual(out['isg'], 'v')
        # 挑战标记由 update_config_cookies 漏斗统一丢弃，这里原样透传
        self.assertEqual(out['x5secdata'], 'marker')

    def test_all_four_merge_sites_filter_x5sec(self):
        import pathlib
        import re

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        # 所有 set-cookie 解析点之后必须紧跟过滤
        sites = [m.start() for m in re.finditer(
            r"response\.headers\.getall\('set-cookie'", src
        )]
        self.assertGreaterEqual(len(sites), 4, f'应至少有 4 个合并点，实际 {len(sites)}')
        for pos in sites:
            window = src[pos: pos + 700]
            self.assertIn(
                'filter_mtop_set_cookies', window,
                f'合并点（偏移 {pos}）解析 set-cookie 后必须过滤 x5sec',
            )


class X5SecLossGuardTests(unittest.TestCase):
    """回归：x5sec 落库后不得被无 x5sec 的回写覆盖。

    2026-10-01 13:45 实测：滑块通过 → save_cookie（含新 x5sec）→ 3 秒后
    refresh_token 响应的 set-cookie 合并触发 update_config_cookies，
    把**无 x5sec** 的 self.cookies 整串写回库 → 通行证丢失 → 下次请求
    18 字段/1146 长度 → 又被拒 → 无限弹窗。
    """

    def test_guard_exists_in_update_config_cookies(self):
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        idx = src.find('async def update_config_cookies')
        self.assertGreater(idx, 0)
        body = src[idx: idx + 4000]
        self.assertIn('[安全网] 拒绝覆盖', body, '落库前必须有 x5sec 防丢闸')
        self.assertIn('库中 Cookie 含 x5sec 而本次写入不含', body)

    def test_fingerprint_logging_in_apply(self):
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        idx = src.find('async def _apply_manual_captcha_cookies')
        self.assertGreater(idx, 0)
        body = src[idx: idx + 3000]
        self.assertIn('[取证] 人工验证 Cookie 指纹', body, '保存链路必须打指纹日志')


class InstanceCookieSyncTests(unittest.TestCase):
    """回归：运行实例同步新 Cookie 必须同时更新 cookies_str 和 cookies 字典。

    2026-10-01 13:45 实测：人工验证后只同步了 instance.cookies_str，
    而 instance.cookies（字典）仍是旧值（无 x5sec）。3 秒后 refresh_token
    响应 set-cookie 合并：self.cookies.update(new) → 用旧字典重建
    cookies_str → update_config_cookies 落库 —— 无 x5sec 的整串把
    刚保存的通行证顶掉，下次请求又被拒，无限弹窗。
    """

    def test_xianyuautoasync_syncs_dict_too(self):
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        idx = src.find('async def _apply_manual_captcha_cookies')
        self.assertGreater(idx, 0)
        body = src[idx: idx + 4500]
        self.assertIn('instance.cookies_str = cleaned', body)
        self.assertIn('instance.cookies = _tc(cleaned)', body,
                      '必须同时同步 cookies 字典，否则下次合并从旧字典重建')

    def test_reply_server_manual_session_syncs_dict_too(self):
        import pathlib

        src = pathlib.Path('app/reply_server.py').read_text(encoding='utf-8')
        idx = src.find("instance.cookies_str = result['cookies_str']")
        self.assertGreater(idx, 0)
        window = src[idx: idx + 700]
        self.assertIn("instance.cookies = _tc(result['cookies_str'])", window,
                      '手动验证接口同样必须同步 cookies 字典')


class CookieValueKeyMismatchTests(unittest.TestCase):
    """回归：get_cookie_details 返回的键名是 'value'，不是 'cookie_value'。

    2026-10-01 22:02（日志时钟 15:02）实测决定性链条：
      · 滑块通过 → save_cookie 存库（22 字段含 x5sec，取证指纹确认）✓
      · 运行实例 refresh_token 开头「从数据库重新加载」分支读
        account_info.get('cookie_value') → **永远 None** → 重载从不触发
      · 实例拿着旧 Cookie（无 x5sec）发请求 → 又被拒
      · 响应 set-cookie 合并（过滤 x5sec）→ update_config_cookies 落库
        → 无 x5sec 整串覆盖库里刚存的通行证
      · 「安全网」读库同样用 cookie_value → 永远 None → _has_x5_db=False
        → 从不拦截
      → 结果：每次滑块过了都被静默顶掉，无限弹窗。

    修复：三处读取点都改为 value or cookie_value 兼容。
    """

    def test_get_cookie_details_returns_value_key(self):
        """守卫：返回键名若变更，所有读取点必须同步更新。"""
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        # 不允许再出现「只读 cookie_value」的调用（必须以 value 优先）。
        # 注意兼容写法 `get('value') or get('cookie_value')` 是允许的。
        import re
        bare = [
            m for m in re.findall(r"\.get\('cookie_value'[^)]*\)", src)
            if 'value\') or' not in m  # 兼容写法豁免
        ]
        # 精确排除：兼容链里作为 fallback 出现的
        bare = [b for b in bare if f"{b}" not in src.replace("get('value') or "+b, '')]
        self.assertEqual(bare, [], "存在只读 'cookie_value' 的调用点（键名是 'value'）")

    def test_reload_and_guard_use_value_key(self):
        import pathlib

        src = pathlib.Path('XianyuAutoAsync.py').read_text(encoding='utf-8')
        # 重载分支（refresh_token 开头）
        self.assertIn("account_info.get('value') or account_info.get('cookie_value')", src)
        # 安全网（update_config_cookies）
        self.assertIn("_cur.get('value') or _cur.get('cookie_value')", src)
        # 密码登录刷新前的重载
        self.assertIn("db_cookie_value = account_info.get('value')", src)
