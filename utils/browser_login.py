"""用本地有头浏览器完成闲鱼登录，并回收完整 Cookie（含 x5sec）。

和纯接口扫码链路的区别
----------------------
登录、滑块、人脸/实名验证全部发生在**真实浏览器**里，平台看到的是真实指纹，
因此不会出现下面这些死结：

- 扫码确认了却拿不到登录态：ivCheckLogin.htm 是浏览器端 JS + iframe 完成的，
  服务端接口根本不下发 unb；
- 滑块拖到位仍然 FAIL_SYS_USER_VALIDATE：自动化派发的鼠标事件密度不对；
- 风控要求验证时只能干等：没有可交互的浏览器，用户无处下手。

浏览器 profile 与后续流程共用（见 utils/browser_profile.py），
所以"这次登录"和"以后过验证"在平台看来是同一台设备、同一份历史。
"""
import asyncio
import time
from typing import Any, Dict, Optional

from loguru import logger

# 与账号密码登录流程用的是同一个入口：/im 会强制跳登录，登录成功后正好停在
# 消息页 —— 既触发登录，又能顺带证明登录态真的可用。
DEFAULT_LOGIN_URL = 'https://www.goofish.com/im'
DEFAULT_TIMEOUT = 300          # 等用户操作的秒数
POLL_INTERVAL = 2.0            # 轮询 unb 的间隔
SETTLE_SECONDS = 15            # 拿到 unb 后再等一会，让平台把配套 Cookie 下发完
# 校验「登录态是否真生效」的最小间隔。这个校验要打一次 mtop，
# 不能每 2 秒来一发（会把刚登录的账号又推进风控）。
VERIFY_INTERVAL = 15

# 正在等待人工完成验证的登录窗口：{label: 打开时间}。
#
# 保留窗口是为了让用户在同一个浏览器里过完滑块（不再新开一个），但它会占着
# profile，导致后续点击看起来“没反应”（2026-09-29 实测）。这里登记下来，
# 便于后续请求直接给出明确提示，而不是默默等锁。
_PENDING_VERIFICATION_WINDOWS: Dict[str, float] = {}


async def _start_playwright():
    """优先 patchright（能把 navigator.webdriver 从 true 变成 false）。"""
    try:
        from patchright.async_api import async_playwright as patchright_async
        return await patchright_async().start(), 'patchright'
    except Exception as exc:
        logger.warning(f'patchright 不可用（{type(exc).__name__}），回退 playwright')
        from playwright.async_api import async_playwright
        return await async_playwright().start(), 'playwright'


def _collect_cookies(cookies: list) -> str:
    return '; '.join(f"{c['name']}={c['value']}" for c in cookies if c.get('name'))


def _find_unb(cookies: list) -> str:
    for cookie in cookies:
        if cookie.get('name') == 'unb' and cookie.get('value'):
            return str(cookie['value'])
    return ''


def _session_usable_from_token_response(payload: dict) -> bool:
    """Token 接口的响应能不能证明「登录态真的有效」。

    为什么不能只看 cookies 里有没有 unb：浏览器用的是持久化共享 profile，
    里面可能残留上一次的旧会话（unb / cookie2 都在，但服务端已经不认）。
    实测后果：点了「本地浏览器登录」，窗口刚开就被判「检测到登录成功」
    并关掉，浏览器根本没登录，系统却回报「Cookie 已更新」—— 拿回来的是
    同一份失效 Cookie。

    判定规则：
      - SUCCESS：拿到 accessToken → 有效
      - 需要人机验证（FAIL_SYS_USER_VALIDATE / 惩罚 URL）：会话是有效的，
        只是要过滑块 → 也算有效（不能判成「没登录」，否则用户过完滑块
        还会被告知登录失败）
      - SESSION_EXPIRED / 其它：视为无效，继续等用户真正登录
    """
    if not isinstance(payload, dict):
        return False

    ret = payload.get('ret') or []
    text = ' '.join(str(item) for item in ret)
    if 'SUCCESS' in text:
        return True
    if any(marker in text for marker in ('USER_VALIDATE', 'punish', 'action=captcha', 'RGV587')):
        return True

    data = payload.get('data')
    if isinstance(data, dict):
        url = data.get('url')
        if isinstance(url, str) and ('punish' in url or 'action=captcha' in url):
            return True
    return False


# 验证窗口登记的最长有效期（秒）。人工验证超时设的是 600 秒，窗口实际
# 生命周期不会超过它多少；超过视为残留登记（窗口早已不存在）。
# 背景（2026-10-02 实训）：keep_open 开窗时登记键是 '新账号'（当时还不知道
# 哪个账号），同窗口验证结束却用 target 账号 id 清理 —— 键名错位导致
# 登记永远不清，之后用户再点登录就一直被告知「已有一个验证码窗口打开着
# （4350 秒前）」而他根本看不到窗口。TTL 是最后一道兜底。
PENDING_VERIFICATION_TTL = 20 * 60


def pending_verification_window(label: str) -> Optional[float]:
    """返回正在等待人工验证的窗口打开时间（None = 没有）。

    登记超过 TTL 视为残留自动作废：窗口不可能还开着（验证早超时了），
    继续挡住用户只会让人莫名其妙。
    """
    opened = _PENDING_VERIFICATION_WINDOWS.get(label)
    if opened is not None and time.time() - opened > PENDING_VERIFICATION_TTL:
        _PENDING_VERIFICATION_WINDOWS.pop(label, None)
        logger.warning(
            f'【{label}】验证窗口登记已存在 {int(time.time() - opened)} 秒（超过 TTL '
            f'{PENDING_VERIFICATION_TTL} 秒），鉴定为残留登记并作废（窗口早已关闭）'
        )
        return None
    return opened


def clear_pending_verification_window(label: str) -> None:
    """人工验证结束后清除登记（窗口已关闭）。"""
    _PENDING_VERIFICATION_WINDOWS.pop(label, None)


async def _captcha_pending(context, page) -> bool:
    """当前窗口里是否出现/即将出现滑块验证。

    登录刚成功时滑块可能还没渲染完，所以既看页面里有没有验证码元素，
    也看 URL 是否已跳到惩罚页（``punish``/``x5secdata``/``captcha``）。
    """
    import asyncio as _asyncio

    selectors = [
        '#nocaptcha',
        '#scratch-captcha-btn',
        '.scratch-captcha-container',
        '.scratch-captcha-slider',
        '.nc-container',
    ]

    # 给验证码一点渲染时间（轮询约 3 秒）
    for _ in range(6):
        try:
            url = (page.url or '').lower()
            if any(k in url for k in ('punish', 'x5secdata', 'captcha', 'nocaptcha')):
                logger.info(f'【验证码】登录窗口 URL 已是验证页: {page.url[:110]}')
                return True
        except Exception:
            pass
        try:
            for frame in page.frames:
                for selector in selectors:
                    try:
                        element = await frame.query_selector(selector)
                        if element and await element.is_visible():
                            logger.info(f'【验证码】登录窗口里检测到滑块元素: {selector}')
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        await _asyncio.sleep(0.5)
    return False


async def _verify_session_usable(cookie_id: str, cookies_str: str) -> bool:
    """拿刚取回的 Cookie 打一次 Token 接口，确认登录态真的能用。"""
    import json
    import time

    import aiohttp

    from app.config import API_ENDPOINTS
    from utils.xianyu_utils import trans_cookies, generate_sign, generate_device_id

    try:
        cd = trans_cookies(cookies_str)
        token = (cd.get('_m_h5_tk') or '').split('_')[0]
        if not token:
            return False

        ts = str(int(time.time() * 1000))
        params = {
            'jsv': '2.7.2', 'appKey': '34839810', 't': ts, 'sign': '', 'v': '1.0',
            'type': 'originaljson', 'accountSite': 'xianyu', 'dataType': 'json',
            'timeout': '20000', 'api': 'mtop.taobao.idlemessage.pc.login.token',
            'sessionOption': 'AutoLoginOnly',
            'dangerouslySetWindvaneParams': '%5Bobject%20Object%5D',
            'smToken': 'token', 'queryToken': 'sm', 'sm': 'sm',
            'spm_cnt': 'a21ybx.im.0.0', 'spm_pre': 'a21ybx.home.sidebar.1.4c053da6vYwnmf',
            'log_id': '4c053da6vYwnmf',
        }
        device_id = generate_device_id(cd.get('unb', ''))
        data_val = json.dumps(
            {'appKey': '444e9908a51d1cb236a27862abc769c9', 'deviceId': device_id}
        )
        params['sign'] = generate_sign(params['t'], token, data_val)

        headers = {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36'
            ),
            'referer': 'https://www.goofish.com/',
            'origin': 'https://www.goofish.com',
            'cookie': cookies_str,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                API_ENDPOINTS.get('token'),
                params=params,
                data={'data': data_val},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                payload = json.loads(await resp.text())
        return _session_usable_from_token_response(payload)
    except Exception as exc:
        # 网络异常不能当成「登录无效」，否则用户刚登完就被判失败
        logger.debug(f'【{cookie_id}】校验登录态失败（视为不可用）: {exc}')
        return False


def _config_defaults() -> tuple:
    """从 global_config.yml → BROWSER_LOGIN 读默认登录入口与等待上限。"""
    try:
        from app.config import BROWSER_LOGIN

        cfg_url = str(BROWSER_LOGIN.get('url') or '').strip() or DEFAULT_LOGIN_URL
        cfg_timeout = int(BROWSER_LOGIN.get('timeout') or DEFAULT_TIMEOUT)
        return cfg_url, cfg_timeout
    except Exception:
        return DEFAULT_LOGIN_URL, DEFAULT_TIMEOUT


async def open_login_session(
    cookie_id: Optional[str] = None,
    timeout: Optional[int] = None,
    url: Optional[str] = None,
    headless: bool = False,
    keep_open_if_captcha: bool = False,
) -> Dict[str, Any]:
    """打开一个可见的浏览器窗口，等用户在里面完成登录，然后回收 Cookie。

    Args:
        cookie_id: 目标账号 ID。给定时用该账号自己的 profile（用于"重新登录已有
            账号"）；不给则用临时 profile，登录拿到 unb 后再迁移成正式 profile
            （用于"添加新账号"）。
        timeout: 等用户操作的秒数上限。
        url: 登录入口，默认 https://www.goofish.com/im 。
        headless: 是否无头。默认有头 —— 这是给用户自己操作用的窗口。
        keep_open_if_captcha: 登录成功后发现验证码/风控时，**不关闭窗口**，
            把 ``context``/``page`` 一并返回给调用方继续人工验证。否则用户会
            看到“验证码一闪而过、随后又弹一个新浏览器”。返回结果里会带
            ``keep_open=True``，此时调用方负责关闭窗口。

    Returns:
        ``{"success": bool, "message": str, "cookies_str": str, "unb": str}``
    """
    from utils.browser_profile import (
        acquire_profile_async,
        clean_singleton_lock_files,
        launch_shared_context,
        profile_dir,
        release_profile_async,
        staging_profile_dir,
    )

    target_url = (url or _config_defaults()[0]).strip()
    try:
        wait_seconds = max(30, min(int(timeout or _config_defaults()[1]), 1800))
    except (TypeError, ValueError):
        wait_seconds = DEFAULT_TIMEOUT

    if cookie_id:
        profile_path = profile_dir(cookie_id)
    else:
        profile_path = staging_profile_dir()

    label = cookie_id or '新账号'
    if not headless:
        # 上次被强杀留下的锁会让新窗口行为异常（页面能动、一操作就卡住）
        clean_singleton_lock_files(profile_path, label=label)

    logger.info(f'【{label}】准备打开本地浏览器登录，profile: {profile_path}')
    logger.info(f'【{label}】登录入口: {target_url}（等待上限 {wait_seconds} 秒）')

    playwright = None
    context = None
    profile_held = False
    result: Dict[str, Any] = {
        'success': False,
        'message': '',
        'cookies_str': '',
        'unb': '',
        'profile_dir': profile_path,
    }

    # 已有窗口在等人工过验证时，直接说清楚，不要默默等锁（用户会以为“点了没
    # 反应”）。这类窗口本身就占着 profile，再开一个也开不出来。
    pending_since = pending_verification_window(label)
    if pending_since is not None:
        waited = int(time.time() - pending_since)
        result['message'] = (
            f'已有一个验证码窗口打开着（{waited} 秒前），'
            f'请先在那个窗口里完成验证；若那个窗口已经关了，请稍后重试'
        )
        logger.warning(f'【{label}】{result["message"]}')
        return result

    try:
        # 同一账号的 profile 是独占的：已经有别的流程在用它（过验证、取订单…）时，
        # 宁可现在就说清楚，也不要开出第二个浏览器把 profile 弄成半损坏状态。
        # 但等锁上限要短：人工验证窗口会长期占着 profile，等 300 秒只会让用户
        # 以为“点了没反应”（2026-09-29 实测：两次点击各卡了 5 分钟）。
        await acquire_profile_async(cookie_id or None, '本地浏览器登录', timeout=20)
        profile_held = True

        playwright, engine = await _start_playwright()
        logger.info(f'【{label}】浏览器内核: {engine}')

        # 优先系统正式版 Chrome，失败回退内置 Chromium ——
        # 实测自带的 Chromium 指纹会被阿里 nc 直接识破，连真人手动拖动都判定失败。
        context = await launch_shared_context(
            playwright, profile_path, headless=headless, purpose='本地浏览器登录'
        )

        page = context.pages[0] if getattr(context, 'pages', None) else await context.new_page()

        window_closed = {'value': False}

        def _mark_closed(*_args, **_kwargs):
            window_closed['value'] = True

        try:
            page.on('close', _mark_closed)
            context.on('close', _mark_closed)
        except Exception:
            pass

        try:
            await page.goto(target_url, wait_until='domcontentloaded', timeout=60000)
        except Exception as goto_error:
            # 导航超时不算致命：登录页可能已经在加载了
            logger.warning(f'【{label}】打开登录页告警: {type(goto_error).__name__}')

        logger.info(
            f'【{label}】浏览器窗口已打开，请在弹出的窗口里完成登录'
            f'（扫码 / 账号密码 / 滑块 / 人脸验证都可以）'
        )

        deadline = time.time() + wait_seconds
        unb = ''
        session_ok = False
        last_verify = 0.0
        while time.time() < deadline:
            if window_closed['value']:
                result['message'] = '浏览器窗口被关闭，登录未完成'
                logger.warning(f'【{label}】{result["message"]}')
                return result

            try:
                cookies = await context.cookies()
            except Exception as exc:
                result['message'] = f'浏览器已关闭（{type(exc).__name__}），登录未完成'
                logger.warning(f'【{label}】{result["message"]}')
                return result

            unb = _find_unb(cookies)
            if unb:
                # 光有 unb 不代表登录成功：这是持久化共享 profile，里面可能残留
                # 上一次的旧会话（unb / cookie2 都在，但服务端已经不认）。实测后果
                # 是窗口刚开就被判「登录成功」并关掉，浏览器其实没登录，系统却
                # 回报「Cookie 已更新」—— 拿回来的是同一份失效 Cookie。
                # 所以必须拿实际接口验一次。
                if time.time() - last_verify >= VERIFY_INTERVAL:
                    last_verify = time.time()
                    session_ok = await _verify_session_usable(label, _collect_cookies(cookies))
                    if not session_ok:
                        logger.warning(
                            f'【{label}】检测到 unb={unb}，但登录态未生效'
                            f'（可能是浏览器里的旧会话），继续等待你完成登录…'
                        )
                if session_ok:
                    logger.info(f'【{label}】检测到登录成功（unb={unb}），等待 Cookie 下发完整')
                    break

            await asyncio.sleep(POLL_INTERVAL)

        if not unb or not session_ok:
            result['message'] = (
                f'等待 {wait_seconds} 秒仍未检测到有效登录'
                f'（浏览器里可能残留旧会话，登录态已失效），请重试'
                if unb else
                f'等待 {wait_seconds} 秒仍未检测到登录，请重试'
            )
            logger.warning(f'【{label}】{result["message"]}')
            return result

        # 登录刚成功时 sgcookie / _m_h5_tk / x5sec 可能还没下发完，
        # 直接取走会出现"Cookie 拿回来了但接口仍然用不了"。
        settle_deadline = time.time() + SETTLE_SECONDS
        while time.time() < settle_deadline:
            try:
                cookies = await context.cookies()
            except Exception:
                break
            names = {c['name'] for c in cookies}
            if {'unb', 'cookie2', '_m_h5_tk'} <= names:
                break
            await asyncio.sleep(1.0)

        cookies = await context.cookies()
        cookies_str = _collect_cookies(cookies)
        unb = _find_unb(cookies) or unb
        names = sorted({c['name'] for c in cookies})

        result.update({
            'success': True,
            'cookies_str': cookies_str,
            'unb': unb,
            'message': f'登录成功（unb={unb}，Cookie {len(names)} 个字段）',
        })

        # 登录成功的瞬间往往紧接着弹一个滑块验证。以前这里直接 return，
        # finally 无条件 context.close() —— 用户看到的正是“验证码只出现不到
        # 2 秒窗口就被关了”（2026-09-29 用户反馈）。
        # 这里改为：若检测到验证码/风控，就**保留本窗口**交给人工验证，
        # 不再新开浏览器，满足“全部流程在同一个有头浏览器内”。
        if keep_open_if_captcha and await _captcha_pending(context, page):
            result['keep_open'] = True
            result['context'] = context
            result['page'] = page
            _PENDING_VERIFICATION_WINDOWS[label] = time.time()
            logger.warning(
                f'【{label}】检测到验证码/风控，保留本窗口交给人工验证'
                f'（不关闭、不新开浏览器）'
            )
            return result

        logger.info(
            f'【{label}】登录成功: unb={unb}, 字段数={len(names)}, '
            f'含 x5sec={"x5sec" in names}'
        )
        return result

    except Exception as exc:
        result['message'] = f'浏览器登录异常: {type(exc).__name__}: {exc}'
        logger.error(f'【{label}】{result["message"]}')
        return result
    finally:
        # 窗口已被要求保留（交给人工验证）时不得关闭 —— 关掉的话用户又只能看到
        # “验证码一闪而过”，而且接下来还要再弹一个新的浏览器。
        # Playwright 与 profile 占用同样不能释放：窗口还活着就需要它们。
        # 注意：这里绝不能写 return（finally 里 return 会吞掉异常）。
        if not result.get('keep_open'):
            if context is not None:
                try:
                    await context.close()
                    logger.info(f'【{label}】登录用浏览器已关闭（profile 已保留）')
                except Exception as close_error:
                    logger.warning(f'【{label}】关闭登录浏览器失败: {close_error}')
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            if profile_held:
                await release_profile_async(cookie_id or None, '本地浏览器登录')
        else:
            logger.info(
                f'【{label}】登录窗口已保留给人工验证（保留 Playwright 与 profile 占用）'
            )
