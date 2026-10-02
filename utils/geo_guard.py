# -*- coding: utf-8 -*-
"""出口 IP 归属地守卫（GeoGuard）。

背景（2026-10-02 实测）：用户本机出口 IP 是新加坡（代理/VPN），闲鱼对
非中国大陆 IP 的风控明显收紧 —— 滑块验证通过率下降、x5sec 存活期缩短。
前端账号管理里也有对应提示文案。

功能：
- 服务启动时检测一次本机出口 IP 的归属地；
- 之后按固定间隔复查（IP 可能漂移，代理节点可能切换）；
- 出口不在中国大陆/香港/澳门时，通过已配置的通知渠道提醒用户；
- 状态未变化不重复通知（去抖），恢复境内再报一次"已恢复正常"。

对接点：XianyuLive.main() 启动时 spawn 为后台任务。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from loguru import logger

# 出口允许的地区（闲鱼业务可用的境内地区）
CN_REGION_CODES = {"CN", "HK", "MO"}

# 复查间隔（秒）：出口漂移通常是代理节点切换，30 分钟足够敏感
CHECK_INTERVAL = 30 * 60

# 请求超时（秒）
TIMEOUT = 8.0

# 本地结果缓存（进程内）：{key: bool} key 固定为 'outbound'，未来多出口再扩展
_LAST_STATE: dict = {}


class GeoGuard:
    """出口 IP 归属地检测与告警。"""

    # 检测源（按顺序尝试，任一成功即返回）；都免费且国内可达
    SOURCES = (
        "http://ip-api.com/json/?fields=status,country,countryCode,query,isp",
        "https://api.ip.sb/geoip",
        "https://ipapi.co/json/",
    )

    def __init__(self, notify_func=None, cookie_id: str = "system"):
        """
        Args:
            notify_func: 通知回调 async (title, message, category) -> None，
                通常传 XianyuLive.send_token_refresh_notification 的包装。
                None 时只记日志不推送。
            cookie_id: 归属账号（通知里显示用），system 表示服务级。
        """
        self.notify_func = notify_func
        self.cookie_id = cookie_id

    # ------------------------------------------------------------------
    async def fetch_geo(self) -> Optional[dict]:
        """查询本机出口 IP 归属地。失败返回 None（绝不因检测失败抛错）。"""
        for url in self.SOURCES:
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(url)
                    data = resp.json()
            except Exception as e:
                logger.debug(f"出口IP检测源 {url} 失败: {type(e).__name__}: {e}")
                continue

            # ip-api: {status, country, countryCode, query, isp}
            # ip.sb/ipapi.co: {ip, country(名), country_code/organization}
            ip = data.get("query") or data.get("ip")
            code = (
                data.get("countryCode")
                or data.get("country_code")
                or ""
            )
            if not ip or not code:
                continue
            return {
                "ip": ip,
                "country_code": code.upper(),
                "country": data.get("country") or "",
                "isp": data.get("isp") or data.get("organization") or "",
                "source": url,
            }
        return None

    # ------------------------------------------------------------------
    async def check_and_notify(self, force: bool = False) -> Optional[bool]:
        """执行一次检测。返回 True=境内，False=境外，None=检测失败。

        状态翻转时发通知：境内→境外 报警，境外→境内 报恢复。
        首次检测总是报告当面结果。
        """
        geo = await self.fetch_geo()
        if geo is None:
            logger.warning("出口IP归属地检测失败（所有检测源不可达），跳过本次")
            return None

        domestic = geo["country_code"] in CN_REGION_CODES
        prev = _LAST_STATE.get("outbound")

        if prev is None or prev != domestic or force:
            _LAST_STATE["outbound"] = domestic
            if domestic:
                logger.info(
                    f"出口IP: {geo['ip']} ({geo['country']}/{geo['country_code']}, "
                    f"{geo['isp']}) —— 境内，符合闲鱼风控要求"
                )
                if prev is False and geo and self.notify_func:
                    await self._notify(
                        "✅ 出口IP已恢复境内",
                        f"出口IP: {geo['ip']}\n归属: {geo['country']} ({geo['country_code']})\n"
                        f"运营商: {geo['isp'] or '未知'}\n\n"
                        f"闲鱼风控对境外 IP 收紧，恢复境内后滑块通过率和账号存活期都会改善。",
                    )
            else:
                logger.warning(
                    f"出口IP: {geo['ip']} ({geo['country']}/{geo['country_code']}, "
                    f"{geo['isp']}) —— 不在中国大陆/港澳，闲鱼风控将收紧"
                )
                if self.notify_func:
                    await self._notify(
                        "⚠️ 出口IP在中国境外，闲鱼风控会收紧",
                        f"出口IP: {geo['ip']}\n"
                        f"归属: {geo['country']} ({geo['country_code']})\n"
                        f"运营商: {geo['isp'] or '未知'}\n\n"
                        f"检测到当前出口不在国内（多半是代理/VPN 节点）。"
                        f"闲鱼对境外 IP 风控极严：滑块更容易被拦、通过后凭证有效期更短。\n"
                        f"建议给闲鱼相关进程配置直连（分流规则），或关闭代理后重试。",
                    )
        return domestic

    # ------------------------------------------------------------------
    async def _notify(self, title: str, message: str):
        try:
            await self.notify_func(f"{title}\n\n{message}", "ip_geo_warning")
        except Exception as e:
            logger.error(f"出口IP告警通知发送失败: {e}")

    # ------------------------------------------------------------------
    async def run_forever(self, first_delay: float = 10.0):
        """常驻任务：启动后先检一次，之后按间隔复查。"""
        await asyncio.sleep(first_delay)
        while True:
            try:
                await self.check_and_notify()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"出口IP守卫任务异常: {type(e).__name__}: {e}")
            await asyncio.sleep(CHECK_INTERVAL)


def create_geo_guard_task(notify_func=None, cookie_id: str = "system") -> asyncio.Task:
    """创建常驻守卫任务（调用方应挂到后台任务集）。"""
    guard = GeoGuard(notify_func=notify_func, cookie_id=cookie_id)
    return asyncio.create_task(guard.run_forever())
