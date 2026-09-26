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
) -> Dict[str, Any]:
    """打开一个可见的浏览器窗口，等用户在里面完成登录，然后回收 Cookie。

    Args:
        cookie_id: 目标账号 ID。给定时用该账号自己的 profile（用于"重新登录已有
            账号"）；不给则用临时 profile，登录拿到 unb 后再迁移成正式 profile
            （用于"添加新账号"）。
        timeout: 等用户操作的秒数上限。
        url: 登录入口，默认 https://www.goofish.com/im 。
        headless: 是否无头。默认有头 —— 这是给用户自己操作用的窗口。

    Returns:
        ``{"success": bool, "message": str, "cookies_str": str, "unb": str}``
    """
    from utils.browser_profile import (
        clean_singleton_lock_files,
        launch_shared_context,
        profile_dir,
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
    result: Dict[str, Any] = {
        'success': False,
        'message': '',
        'cookies_str': '',
        'unb': '',
        'profile_dir': profile_path,
    }

    try:
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
                logger.info(f'【{label}】检测到登录成功（unb={unb}），等待 Cookie 下发完整')
                break

            await asyncio.sleep(POLL_INTERVAL)

        if not unb:
            result['message'] = f'等待 {wait_seconds} 秒仍未检测到登录，请重试'
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
