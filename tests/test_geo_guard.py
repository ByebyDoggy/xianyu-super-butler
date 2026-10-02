# -*- coding: utf-8 -*-
"""出口IP归属地守卫测试。"""
import unittest
from utils.geo_guard import GeoGuard, CN_REGION_CODES, _LAST_STATE


class GeoGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _LAST_STATE.clear()

    async def test_domestic_ok(self):
        g = GeoGuard()
        async def fake(url):
            return None
        g.fetch_geo = fake
        async def patched():
            return {"ip": "1.2.3.4", "country_code": "CN", "country": "China", "isp": "CT", "source": "t"}
        g.fetch_geo = patched
        r = await g.check_and_notify()
        self.assertTrue(r)
        self.assertEqual(_LAST_STATE.get("outbound"), True)

    async def test_overseas_warns_once(self):
        g = GeoGuard()
        sent = []
        async def notify(msg, ntype):
            sent.append((msg, ntype))
        g.notify_func = notify
        async def patched():
            return {"ip": "9.9.9.9", "country_code": "SG", "country": "Singapore", "isp": "DC", "source": "t"}
        g.fetch_geo = patched
        r = await g.check_and_notify()
        self.assertFalse(r)
        self.assertEqual(len(sent), 1)
        self.assertIn("境外", sent[0][0])
        # 状态不变不重复通知
        r = await g.check_and_notify()
        self.assertEqual(len(sent), 1)
        # 恢复境内发恢复通知
        async def domestic():
            return {"ip": "1.2.3.4", "country_code": "CN", "country": "China", "isp": "CT", "source": "t"}
        g.fetch_geo = domestic
        r = await g.check_and_notify()
        self.assertTrue(r)
        self.assertEqual(len(sent), 2)
        self.assertIn("恢复", sent[1][0])

    async def test_all_sources_fail_returns_none(self):
        g = GeoGuard()
        async def none():
            return None
        g.fetch_geo = none
        r = await g.check_and_notify()
        self.assertIsNone(r)

    def test_cn_region_codes(self):
        self.assertEqual(CN_REGION_CODES, {"CN", "HK", "MO"})


if __name__ == '__main__':
    unittest.main()
