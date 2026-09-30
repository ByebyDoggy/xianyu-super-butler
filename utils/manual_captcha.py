"""人工完成滑块验证。

实测发现自动识别已经能把滑块拖到目标位置（误差 0.3px 以内），但服务端仍
判定失败 —— 拦的是行为特征而不是位置精度，因此继续加大自动重试不会有结果。

项目里本来就有远程控制通道（``utils.captcha_remote_control`` 负责推屏和
转发鼠标事件，``static/captcha_control.html`` 是操作页面），但一直没有代码
调用它。这个模块把它接起来：打开登录页，等人在浏览器里完成验证，然后取回
新的 Cookie。

与 ``utils.xianyu_slider_stealth`` 的区别是那边用同步 Playwright，无法和
异步的远程通道共享 page 对象，所以这里独立起一个异步浏览器。
"""

import asyncio
import time
from typing import Any, Dict, Optional

from loguru import logger
from app.config import browser_headless
from utils.xianyu_utils import trans_cookies


# goofish 的 Cookie 域名（包含子域）。滑块通过后的 x5sec 只在这个域上有效。
_GOOFISH_DOMAINS = ('.goofish.com', 'goofish.com')


def _is_goofish_cookie(cookie) -> bool:
    """该 Cookie 是否属于 goofish 域。

    必须区分域：滑块通过后惩罚页会 302 到 taobao.com，那里也会下发一个
    同名 x5sec（aserver/device 令牌）。不分域地拼回字符串就会把它当成
    goofish 的挑战凭证存回账号，token 刷新依旧被拒。
    """
    if not isinstance(cookie, dict):
        return False
    domain = str(cookie.get('domain') or '').lower().lstrip('.')
    if not domain:
        # 没有域的（部分上下文）保守放行，交给后续 x5sec 校验兜底
        return True
    return any(domain == d.lstrip('.') or domain.endswith('.' + d.lstrip('.'))
               for d in _GOOFISH_DOMAINS)


async def _goofish_cookies_snapshot(context, prefer: Optional[dict] = None) -> dict:
    """抓下当前浏览器里 goofish 域的全部 Cookie（name -> value）。

    时机很关键：要在检测到新 x5sec 的**那一瞬**调用，晚一步就可能已经
    重定向到 taobao.com 了。

    同名 Cookie 可能在**不同域**各有一份：注入的账号 Cookie 都挂在
    ``.goofish.com``，而惩罚页新下发的通行证 x5sec 常挂在
    ``h5api.m.goofish.com``（host-only）。旧实现用 ``{name: value}``
    字典直存，两份同名 x5sec 只留一份、谁留下全看遍历顺序 ——
    2026-09-30 实测留下的正是注入的**旧**值，刚拖完滑块拿到的新
    x5sec 被丢掉，落库后平台照样拒绝，于是「每过完一次滑块又弹一次」。
    现在同名时取**域更具体**（域名串更长）的那份，并把检测到的新
    x5sec 通过 ``prefer`` 强制写回，双保险。
    """
    try:
        cookies = await context.cookies()
    except Exception:
        return {}
    snapshot = _dedup_goofish(cookies)
    if prefer:
        for name, value in prefer.items():
            if value:
                snapshot[str(name)] = str(value)
    return snapshot


def _dedup_goofish(cookies_list) -> dict:
    """把 goofish 域 Cookie 列表去重成 name -> value。

    同名 Cookie 跨域共存时取**域更具体**的那份（域名串更长）：浏览器发给
    API 的正是它。顺序无关，不受 context.cookies() 遍历顺序影响。
    """
    best: dict = {}
    for c in cookies_list or []:
        if not (isinstance(c, dict) and c.get('name') and _is_goofish_cookie(c)):
            continue
        name = str(c['name'])
        domain = str(c.get('domain') or '')
        if name not in best or len(domain) > len(best[name][0]):
            best[name] = (domain, str(c.get('value', '')))
    return {k: v for k, (_, v) in best.items()}


def _from_cookie_snapshot(snapshot: dict) -> str:
    """把 name->value 快照拼成 Cookie 字符串。"""
    if not snapshot:
        return ''
    return '; '.join(f'{k}={v}' for k, v in snapshot.items())


def _x5sec_values(browser_cookies, domain_only: bool = False) -> set:
    """取浏览器 Cookie 里所有 x5sec 的取值集合。

    domain_only=True 时只看 goofish 域 —— taobao 域也有同名 x5sec，
    混进来会误判“滑块已过”。
    """
    return set(_x5sec_entries(browser_cookies, domain_only=domain_only).keys())


def _x5sec_entries(browser_cookies, domain_only: bool = False) -> dict:
    """取浏览器 Cookie 里所有 x5sec 的 {取值: 域名} 映射。

    需要域名是为了在同名多份时挑**域更具体**（更接近 API 域）的那份。
    """
    if not browser_cookies:
        return {}
    return {
        str(item.get('value', '')): str(item.get('domain') or '')
        for item in browser_cookies
        if isinstance(item, dict)
        and str(item.get('name', '')).lower() == 'x5sec'
        and (not domain_only or _is_goofish_cookie(item))
    }


def _pick_new_x5sec(entries: dict, baseline: set) -> str:
    """从 {取值: 域名} 里挑出基线之外的新 x5sec，域更具体者优先。

    返回空串表示没有新值（验证未完成）。
    """
    new_entries = {v: d for v, d in entries.items() if v and v not in baseline}
    if not new_entries:
        return ''
    return max(new_entries.items(), key=lambda kv: len(kv[1]))[0]


def _has_new_x5sec(browser_cookies, baseline: set, domain_only: bool = False) -> bool:
    """服务端是否**新发**了一个 x5sec（滑块真正通过的凭证）。

    绝不能只判「Cookie 里有没有 x5sec」：账号 Cookie 里往往带着上一次通过时
    留下的旧 x5sec，我们把它注入浏览器后，第一次轮询就会命中，于是会话开启
    2 秒就宣告“完成”、窗口当场关闭 —— 用户根本来不及拖滑块（2026-09-29 15:50
    实测就是这个循环，每 8 秒弹一次）。

    所以只有在 x5sec 的**取值**相对注入前发生变化时才算法成功。
    """
    current = _x5sec_values(browser_cookies, domain_only=domain_only)
    if not current:
        return False
    return any(value and value not in baseline for value in current)

LOGIN_URL = "https://www.goofish.com/"

# 人工操作需要时间，但也不能无限占用浏览器
DEFAULT_TIMEOUT = 300

# 等待滑块真正出现的最长时间（秒）。惩罚页加载本身就要几秒，
# 等不到说明 x5secdata 已失效或该账号当前已不在风控状态。
CAPTCHA_PRESENT_TIMEOUT = 20


def get_verification_url(cookie_id: str) -> Optional[str]:
    """从风控日志里取该账号最近一次滑块惩罚的 URL。

    惩罚 URL（``...punish?x5secdata=...&x5step=2&action=captcha``）是 Token
    刷新响应里 ``data.url`` 返回的，写进了 ``risk_control_logs.event_description``。
    只有导航到这个 URL 才会真正弹出滑块 —— 导航首页（原实现的 LOGIN_URL）不会。

    x5secdata 有有效期（约 1 小时），过期后导航会跳到空白页。所以这里只是
    兜底：会话内若触发实时 Token 刷新拿到新 URL 更好，拿不到才用它。
    """
    import re
    from app.db_manager import db_manager

    try:
        with db_manager.lock:
            cur = db_manager.conn.cursor()
            cur.execute(
                "SELECT event_description FROM risk_control_logs "
                "WHERE cookie_id=? AND event_type='slider_captcha' "
                "ORDER BY id DESC LIMIT 1",
                (cookie_id,),
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.debug(f"【{cookie_id}】读取惩罚 URL 失败: {exc}")
        return None

    if not row or not row[0]:
        return None
    m = re.search(r"URL: (\S+)", row[0])
    url = m.group(1) if m else None
    if url and ("punish" in url or "action=captcha" in url):
        logger.info(f"【{cookie_id}】从风控日志取到惩罚 URL")
        return url
    return None


async def _fetch_live_verification_url(cookie_id: str, cookies_str: str) -> Optional[str]:
    """主动触发一次 Token 刷新，从响应里拿新鲜的惩罚 URL。

    账号处于风控时，``mtop.taobao.idlemessage.pc.login.token`` 接口会在
    ``data.url`` 返回滑块惩罚页地址；账号正常时返回 accessToken，没有该字段。

    这里只读 URL、**不调用** XianyuLive 的自动解滑块流程（那条路径实测失败），
    避免在人工处理前又被自动重试一遍。
    """
    import json
    import time
    import aiohttp
    from app.config import API_ENDPOINTS
    from utils.xianyu_utils import trans_cookies, generate_sign, generate_device_id

    try:
        cd = trans_cookies(cookies_str)
        if "_m_h5_tk" not in cd:
            return None
        token = cd["_m_h5_tk"].split("_")[0]
        if not token:
            return None

        ts = str(int(time.time() * 1000))
        params = {
            "jsv": "2.7.2", "appKey": "34839810", "t": ts, "sign": "", "v": "1.0",
            "type": "originaljson", "accountSite": "xianyu", "dataType": "json",
            "timeout": "20000", "api": "mtop.taobao.idlemessage.pc.login.token",
            "sessionOption": "AutoLoginOnly",
            "dangerouslySetWindvaneParams": "%5Bobject%20Object%5D",
            "smToken": "token", "queryToken": "sm", "sm": "sm",
            "spm_cnt": "a21ybx.im.0.0", "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
            "log_id": "4c053da6vYwnmf",
        }
        device_id = generate_device_id(cd.get("unb", ""))
        data_val = json.dumps(
            {"appKey": "444e9908a51d1cb236a27862abc769c9", "deviceId": device_id}
        )
        params["sign"] = generate_sign(params["t"], token, data_val)

        headers = {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
            "referer": "https://www.goofish.com/",
            "origin": "https://www.goofish.com",
            "cookie": cookies_str,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                API_ENDPOINTS.get("token"),
                params=params,
                data={"data": data_val},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                payload = json.loads(text)
                ret = payload.get("ret", [])
                if any("SUCCESS" in str(r) for r in ret):
                    # 成功返回 accessToken —— 账号当前不在风控，无惩罚页
                    return None
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                url = data.get("url")
                if url and ("punish" in url or "action=captcha" in url):
                    logger.info(f"【{cookie_id}】实时刷新拿到惩罚 URL")
                    return url
        return None
    except Exception as exc:
        logger.debug(f"【{cookie_id}】实时获取惩罚 URL 失败: {exc}")
        return None


async def open_manual_session(
    cookie_id: str,
    cookies_str: str,
    timeout: int = DEFAULT_TIMEOUT,
    headless: Optional[bool] = None,
    verification_url: Optional[str] = None,
    reuse_context=None,
) -> Dict[str, Any]:
    """打开一个人工验证会话，等待人在浏览器里完成滑块。

    Args:
        timeout: 等待人工操作的秒数，超时后放弃并关闭浏览器。
        headless: 是否无头。None 表示跟随全局配置（默认有头）。
            无头虽然可以靠远程通道把页面推给用户，但无头 Chrome 的指纹
            很容易被 nc 识破，人工验证本来就要求“像真人”，所以默认有头；
            无显示器的服务器可设 BROWSER_HEADLESS=true 退回无头。
        verification_url: 滑块惩罚页 URL（可选）。调用方刚刚在 Token 刷新
            响应里拿到的话直接传进来，可以省一次额外请求。
        reuse_context: 复用调用方已经打开的浏览器上下文（同一个可见窗口）。
            扫码登录后紧接着就要过滑块时传进来，用户看到的就是**一个**窗口
            从头做到尾，而不是“一个一闪就关、再弹一个新的”（2026-09-29 用户
            明确要求全部流程在同一个有头浏览器内）。复用时调用方负责该
            上下文的关闭，本函数不得关它。

    Returns:
        ``{"success": bool, "cookies_str": str, "message": str, "session_id": str}``。
        成功时 ``cookies_str`` 是完成验证后的新 Cookie，调用方应保存。
    """
    from playwright.async_api import async_playwright

    from utils.captcha_remote_control import captcha_controller

    if headless is None:
        headless = browser_headless()

    session_id = str(cookie_id)
    result: Dict[str, Any] = {
        "success": False,
        "cookies_str": cookies_str,
        "message": "",
        "session_id": session_id,
    }

    playwright = None
    browser = None
    context = reuse_context
    refresh_task = None
    profile_held = False
    owns_context = reuse_context is None
    try:
        if owns_context:
            # 同一账号的 profile 是独占的（登录 / 滑块 / 取订单共用一份），
            # 先拿到占用再启动，抢不到就明确报错。
            from utils.browser_profile import acquire_profile_async

            await acquire_profile_async(cookie_id, '人工验证码')
            profile_held = True

            playwright = await async_playwright().start()

            # 与登录 / 滑块共用同一个持久化 profile：平台会把“登录”和“过验证”看成
            # 同一台设备（指纹、访问历史、localStorage 全部连续），通过率明显高于
            # 每次全新的一次性上下文。
            from utils.browser_profile import (
                clean_singleton_lock_files,
                launch_shared_context,
                profile_dir,
            )

            profile_path = profile_dir(cookie_id)
            clean_singleton_lock_files(profile_path, label=cookie_id)

            browser = None  # 持久化模式下没有独立 browser 句柄
            context = await launch_shared_context(
                playwright, profile_path, headless=headless, purpose='人工验证码'
            )
        else:
            # 复用调用方窗口：profile 已由调用方持有，不要再抢锁。
            logger.info(f"【{cookie_id}】复用已打开的有头浏览器窗口进行人工验证")

        # 【关键】反检测：隐藏 Playwright/自动化痕迹（navigator.webdriver、
        # window.__playwright 等）。没有这段时，滑块能拖过（拖动行为是人），
        # 但 nc 的环境检测会发现自动化浏览器，服务端直接把这次通过作废 ——
        # x5sec 照发但不被 API 接受，表现为「滑块过了、凭证也存了，
        # Token 刷新照样 FAIL_SYS_USER_VALIDATE，验证码无限弹」。
        # 2026-09-30 全链路实测：用户自家 Chrome 拖出的凭证能用 67 分钟，
        # Playwright 浏览器拖出的凭证秒拒，唯一差异就是环境指纹。
        # 必须在 new_page 之前 add_init_script：只对之后创建的页面生效。
        try:
            from utils.xianyu_slider_stealth import XianyuSliderStealth

            _stealth_inst = XianyuSliderStealth.__new__(XianyuSliderStealth)
            _stealth_inst.pure_user_id = str(cookie_id)
            _stealth_script = _stealth_inst._get_stealth_script(
                _stealth_inst._get_random_browser_features()
            )
            await context.add_init_script(_stealth_script)
            logger.info(
                f"【{cookie_id}】已注入反检测脚本"
                f"（隐藏 navigator.webdriver / Playwright 痕迹，长度 {len(_stealth_script)}）"
            )
        except Exception as stealth_exc:
            logger.warning(f"【{cookie_id}】反检测脚本注入失败: {stealth_exc}")

        if cookies_str:
            await context.add_cookies(_to_playwright_cookies(cookies_str))

        # 记下注入前就存在的 x5sec。账号 Cookie 里往往带着上一次通过时的旧
        # x5sec，注进去后会被误当成“刚过完滑块”（见 _has_new_x5sec 注释）。
        baseline_x5sec = _x5sec_values(await context.cookies())

        page = await context.new_page()

        # 惩罚页 URL 优先：只有导航到它才会弹出滑块。
        # 原实现导航到闲鱼首页，首页没有滑块 → check_completion 立刻误判
        # “已完成” → 浏览器秒关，用户连上控制页时会话已不存在。
        #
        # 调用方刚在 Token 刷新响应里拿到的话直接用（省一次请求）；拿不到
        # 再主动触发一次刷新拿新鲜 URL；仍拿不到才退回 DB 里最近一次惩罚 URL
        # （可能已过期，等滑块时会发现）。
        verification_url = verification_url or await _fetch_live_verification_url(cookie_id, cookies_str)
        if not verification_url:
            verification_url = get_verification_url(cookie_id)
        if not verification_url:
            result["message"] = "未找到该账号的滑块惩罚 URL，无法开启人工验证"
            logger.warning(f"【{cookie_id}】{result['message']}")
            return result

        try:
            await page.goto(verification_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            logger.warning(f"【{cookie_id}】导航惩罚页失败: {exc}")
        # 给验证组件一点渲染时间，否则截图可能是空白
        await asyncio.sleep(2)

        # 等滑块真正出现再建会话 —— 找不到滑块说明 x5secdata 已失效，
        # 此时即使建了会话也拖不出结果，尽早告知用户更合适。
        captcha_appeared = await _wait_for_captcha_present(page, timeout=CAPTCHA_PRESENT_TIMEOUT)
        if not captcha_appeared:
            result["message"] = (
                "已导航到惩罚页但未检测到滑块（x5secdata 可能已过期），"
                "请让账号重新触发风控后重试"
            )
            logger.warning(f"【{cookie_id}】{result['message']}")
            return result

        await captcha_controller.create_session(session_id, page)
        # 持续推送截图，让用户能看到自己的拖动效果
        refresh_task = asyncio.create_task(
            captcha_controller.auto_refresh_screenshot(session_id, interval=1.0)
        )

        logger.warning(
            f"【{cookie_id}】已开启人工验证会话，请在 {timeout} 秒内于"
            f" /static/captcha_control.html?session={session_id} 完成验证"
        )

        # 关键：会话期间浏览器必须保持存活，等用户拖完才返回。
        # 原实现在这里死等 timeout，且 check_completion 在无滑块时误判完成
        # 直接秒关 —— 改为“滑块出现后，持续等待它消失（完成）或超时”。
        #
        # 但只看「滑块元素消失」仍然会误判：惩罚页在拖拽失败/加载中途也会
        # 让 #nocaptcha 短暂不可见，于是 2026-09-29 事故里报「人工验证完成」
        # 并拿回一份只带 x5secdata、没有 x5sec 的 Cookie —— 下一轮接口照样
        # FAIL_SYS_USER_VALIDATE，账号一直不恢复。
        #
        # 穿过滑块的唯一权威凭证是 Cookie 里出现 x5sec，所以以它为准。
        deadline = time.monotonic() + timeout
        completed = False
        goofish_snapshot: dict = {}
        # 【诊断】记录浏览器任意域的 Cookie 变化与页面跳转。
        # 2026-09-30 实测：用户拖完滑块、元素消失，但始终没出现新的
        # goofish x5sec —— 没有这些日志只能靠猜（页面到底把凭证发到了
        # 哪个域？是否跳到了 taobao？）。变化才记，不刷屏。
        last_jar: Optional[dict] = None
        last_page_url: Optional[str] = None
        # 【过期自愈】挑战约 5 分钟过期：拖得太慢时页面看着过了，
        # 服务端早已丢弃、不会下发通行证（2026-09-30 实测 7.5 分钟后
        # 拖动一无所获）。元素消失超过 15 秒仍无 x5sec 就在**同一窗口**
        # 重新加载新滑块让用户再拖，而不是干等到超时。
        elements_gone_at: Optional[float] = None
        renavigations = 0
        render_grace_until = 0.0
        RENAVIGATE_LIMIT = 5
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            all_cookies = None
            try:
                all_cookies = await context.cookies()
            except Exception as exc:
                logger.debug(f"【{cookie_id}】读取浏览器 Cookie 失败: {exc}")
            if all_cookies is not None:
                # ---- 诊断：任意域 Cookie 变化 ----
                try:
                    jar = {
                        (str(c['name']), str(c.get('domain') or '')): str(c.get('value', ''))[:24]
                        for c in all_cookies if isinstance(c, dict) and c.get('name')
                    }
                    if last_jar is None:
                        last_jar = jar
                    else:
                        for k, v in sorted(jar.items()):
                            if k not in last_jar:
                                logger.info(f"【{cookie_id}】[诊断] Cookie 新增: {k[0]}@{k[1]}={v}…")
                            elif last_jar[k] != v:
                                logger.info(
                                    f"【{cookie_id}】[诊断] Cookie 变更: {k[0]}@{k[1]}:"
                                    f" {last_jar[k]}… → {v}…"
                                )
                        for k in sorted(last_jar):
                            if k not in jar:
                                logger.info(f"【{cookie_id}】[诊断] Cookie 移除: {k[0]}@{k[1]}")
                        last_jar = jar
                except Exception:
                    pass
                # ---- x5sec 检测（goofish 域，取新值） ----
                try:
                    fresh_entries = _x5sec_entries(all_cookies, domain_only=True)
                    new_x5sec = _pick_new_x5sec(fresh_entries, baseline_x5sec)
                    if new_x5sec:
                        # 立即从浏览器里取出 **goofish 域** 的全部 cookie 存下来，
                        # 不等后续重定向污染 cookie jar；并把刚检测到的新 x5sec
                        # 强制写进快照（prefer）—— 同名多份时去重顺序不可靠，
                        # 2026-09-30 实测会把注入的旧值存回去、丢掉新通行证。
                        goofish_snapshot = await _goofish_cookies_snapshot(
                            context, prefer={'x5sec': new_x5sec}
                        )
                        completed = True
                        logger.info(f"【{cookie_id}】检测到新的 goofish x5sec，滑块验证已完成")
                        break
                except Exception as exc:
                    logger.debug(f"【{cookie_id}】检查新 x5sec 失败: {exc}")
            # ---- 诊断：页面跳转 ----
            try:
                url_now = page.url
                if last_page_url is None:
                    last_page_url = url_now
                elif url_now != last_page_url:
                    logger.info(
                        f"【{cookie_id}】[诊断] 页面跳转: …{last_page_url[-80:]} → …{url_now[-80:]}"
                    )
                    last_page_url = url_now
            except Exception:
                pass
            try:
                if await captcha_controller.check_completion(session_id):
                    # 元素消失 ≠ 验证通过（可能只是页面抖动），必须以 x5sec 为准。
                    # 这里给一次短暂宽限：可能刚好拖完、Cookie 还在落盘。
                    for _ in range(3):
                        await asyncio.sleep(1)
                        fresh_entries = _x5sec_entries(await context.cookies(), domain_only=True)
                        new_x5sec = _pick_new_x5sec(fresh_entries, baseline_x5sec)
                        if new_x5sec:
                            goofish_snapshot = await _goofish_cookies_snapshot(
                                context, prefer={'x5sec': new_x5sec}
                            )
                            completed = True
                            break
                    if not completed:
                        logger.warning(
                            f"【{cookie_id}】滑块元素已消失，但 Cookie 里仍未出现 goofish x5sec，"
                            f"继续等待人工完成（不要关窗口）"
                        )
                        # 【过期自愈】元素消失超过 15 秒仍无 x5sec：大概率挑战
                        # 已过期（拖得太慢，服务端不发通行证）。同一窗口里加载
                        # 新滑块让用户再拖，而不是干等到超时。
                        now_mono = time.monotonic()
                        if elements_gone_at is None:
                            elements_gone_at = now_mono
                        elif (
                            now_mono - elements_gone_at > 15
                            and renavigations < RENAVIGATE_LIMIT
                            and now_mono > render_grace_until
                        ):
                            fresh_url = await _fetch_live_verification_url(
                                cookie_id, cookies_str
                            )
                            if fresh_url:
                                try:
                                    await page.goto(
                                        fresh_url, wait_until="domcontentloaded", timeout=60000
                                    )
                                    renavigations += 1
                                    elements_gone_at = None
                                    render_grace_until = time.monotonic() + 30
                                    logger.warning(
                                        f"【{cookie_id}】滑块已消失超过 15 秒且未取得 x5sec"
                                        f"（大概率挑战已过期），已在同一窗口加载新的滑块"
                                        f"（第 {renavigations} 次），请尽快拖动"
                                    )
                                except Exception as nav_exc:
                                    logger.warning(f"【{cookie_id}】重新加载滑块失败: {nav_exc}")
                                    elements_gone_at = now_mono
                            else:
                                logger.warning(
                                    f"【{cookie_id}】实时探测未返回新惩罚 URL"
                                    f"（账号可能已恢复，或探测失败），稍后再试"
                                )
                                elements_gone_at = now_mono
                    if completed:
                        break
                else:
                    # 滑块还在（或新滑块已渲染）—— 重置过期计时
                    elements_gone_at = None
            except Exception as exc:
                logger.debug(f"【{cookie_id}】检查验证完成状态失败: {exc}")

        if not completed:
            result["message"] = f"人工验证超时（{timeout} 秒内未通过，或未取得 x5sec）"
            logger.warning(f"【{cookie_id}】{result['message']}")
            return result

        # 用**检测到 x5sec 那一刻**抓下的 goofish 域快照，而不是现在再读
        # context.cookies()：此刻页面可能已 302 到 taobao.com，那里的 x5sec
        # 是 aserver 令牌，存回账号会让 goofish 继续拒绝（表现为“每过完一次
        # 滑块又弹一次”）。
        new_cookies = _from_cookie_snapshot(goofish_snapshot)
        if not new_cookies:
            # 兜底同样要同名去重（域更具体者优先），不能裸拼：
            # 同名 x5sec 多份时顺序决定谁被留下，不可靠。
            new_cookies = _from_cookie_snapshot(_dedup_goofish(await context.cookies()))
        if not new_cookies:
            result["message"] = "验证已完成但未取到 Cookie"
            return result
        # 双保险：成功路径上必须真的带 x5sec，不能拿挑战中的 Cookie 去覆盖
        try:
            if "x5sec" not in {k.lower() for k in trans_cookies(new_cookies)}:
                result["message"] = "验证已完成但新 Cookie 缺少 x5sec，已放弃覆盖"
                logger.warning(f"【{cookie_id}】{result['message']}")
                return result
        except Exception:
            pass

        result["success"] = True
        result["cookies_str"] = new_cookies
        result["message"] = "人工验证完成"
        logger.info(f"【{cookie_id}】人工验证完成，已取得新 Cookie")
        return result
    except Exception as exc:
        result["message"] = f"人工验证会话异常: {exc}"
        logger.error(f"【{cookie_id}】{result['message']}")
        return result
    finally:
        if refresh_task and not refresh_task.done():
            refresh_task.cancel()
        try:
            from utils.captcha_remote_control import captcha_controller

            await captcha_controller.close_session(session_id)
        except Exception:
            pass
        for closer in ((context, browser) if owns_context else ()):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
        if profile_held:
            from utils.browser_profile import release_profile_async

            await release_profile_async(cookie_id, '人工验证码')
        if not owns_context:
            logger.info(
                f"【{cookie_id}】人工验证结束，保留复用的浏览器窗口（由调用方关闭）"
            )


async def _wait_for_captcha_present(page, timeout: int = CAPTCHA_PRESENT_TIMEOUT) -> bool:
    """等页面里出现可见的滑块/验证码元素。

    原实现不区分“页面没有滑块”和“滑块验证完成” —— 首页没滑块，于是
    check_completion 误判完成。这里显式等滑块出现，避免把“没弹出滑块”
    当成“已完成”。
    """
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # noqa: F401

    # 与 captcha_remote_control.check_completion 用同一组选择器，保证判断一致
    selectors = [
        "#nocaptcha",
        "#scratch-captcha-btn",
        ".scratch-captcha-container",
        ".scratch-captcha-slider",
        '[id*="captcha"]',
        ".nc-container",
    ]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # 先查主页面，再查 iframe（阿里滑块通常包在 iframe 里）
        for frame in page.frames:
            for selector in selectors:
                try:
                    element = await frame.query_selector(selector)
                    if element and await element.is_visible():
                        logger.info(f"检测到滑块元素: {selector} (frame: {frame.url[:80]})")
                        return True
                except Exception:
                    continue
        await asyncio.sleep(1)
    return False


def _to_playwright_cookies(cookies_str: str) -> list:
    """把 Cookie 字符串转成 Playwright 需要的结构。"""
    cookies = []
    for part in cookies_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".goofish.com",
            "path": "/",
        })
    return cookies


def _from_playwright_cookies(cookies: list) -> str:
    """把 Playwright 的 Cookie 列表拼回字符串。"""
    pairs = []
    for cookie in cookies or []:
        name = cookie.get("name")
        if not name:
            continue
        pairs.append(f"{name}={cookie.get('value', '')}")
    return "; ".join(pairs)
