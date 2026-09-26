"""每个闲鱼账号一个持久化浏览器 profile，所有浏览器流程共用同一份。

为什么必须共用
--------------
阿里 nc 查的不只是 Cookie，还包括浏览器层面的指纹：WebGL 渲染器、字体列表、
navigator.plugins、屏幕参数，以及 profile 目录里的历史（localStorage / IndexedDB /
cache / 已访问域名）。项目里有一条实测结论：Playwright 自带的 Chromium
（Chrome for Testing）会被直接识破，**连真人手动拖动滑块都判定失败**；
换成"系统正式版 Chrome + 持久化用户目录"后，同样的手动拖动就能通过。

所以：
- 登录时用哪个 profile，后面过验证、刷 Cookie 就必须用同一个；
- 否则平台会认为"换了台设备"，验证通过也拿不到登录态。

历史遗留目录
------------
改造前是三套互不相干的目录：
- 滑块验证      browser_data/slider_<id>
- 密码登录      browser_data/user_<id>
- 人工验证      压根没有持久化（每次都是全新一次性上下文）

这里统一为 browser_data/account_<id>，并把老的 slider_<id> 目录**迁移**过来 ——
那些目录里已经有"养"出来的真实指纹和访问历史，丢掉反而更容易被风控盯上。
"""
import asyncio
import json
import os
import shutil
import threading
from contextlib import contextmanager
from typing import Any, Dict

from loguru import logger

BROWSER_DATA_DIR = 'browser_data'

# 新登录但还不知道账号 ID 时用的临时目录；登录拿到 unb 后会被迁移成正式目录
STAGING_PROFILE_NAME = 'account_staging_login'


def safe_profile_name(cookie_id: str) -> str:
    """把账号 ID 收敛成安全的目录名片段。

    账号 ID 是 unb（纯数字），但这是拼路径的地方，仍然按白名单过滤一次，
    避免任何形式的路径穿越。
    """
    cleaned = ''.join(
        ch for ch in str(cookie_id or '') if ch.isalnum() or ch in '-_.'
    )
    return cleaned or 'unknown'


def data_root() -> str:
    return os.path.join(os.getcwd(), BROWSER_DATA_DIR)


def staging_profile_dir() -> str:
    """新账号登录阶段的临时 profile 目录。"""
    path = os.path.join(data_root(), STAGING_PROFILE_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def profile_dir(cookie_id: str, migrate: bool = True) -> str:
    """账号的持久化 profile 目录（不存在则创建）。

    migrate=True 时会把老的 slider_<id> / user_<id> 目录迁移过来，
    保住已经养好的指纹与历史。
    """
    safe_id = safe_profile_name(cookie_id)
    root = data_root()
    target = os.path.join(root, f'account_{safe_id}')

    if migrate:
        _migrate_legacy(root, target, safe_id, cookie_id)
    os.makedirs(target, exist_ok=True)
    return target


def adopt_staging_profile(cookie_id: str) -> None:
    """登录成功后，把临时 profile 迁移成该账号的正式 profile。

    这样"这一次浏览器登录"用的上下文，就是"以后过验证"用的上下文 ——
    指纹与历史完全连续。
    """
    staging = os.path.join(data_root(), STAGING_PROFILE_NAME)
    if not os.path.isdir(staging):
        return

    safe_id = safe_profile_name(cookie_id)
    target = os.path.join(data_root(), f'account_{safe_id}')

    if os.path.isdir(target) and os.listdir(target):
        # 已经有正式 profile（通常是"给已有账号重新登录"），保留现成的，
        # 只把临时目录清掉，避免留下垃圾目录。
        try:
            shutil.rmtree(staging, ignore_errors=True)
            logger.info(f'【{cookie_id}】已有 profile，清理临时登录目录')
        except Exception as exc:
            logger.warning(f'【{cookie_id}】清理临时登录目录失败: {exc}')
        return

    try:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(staging, target)
        logger.info(f'【{cookie_id}】临时登录 profile 已转为该账号正式 profile')
    except Exception as exc:
        logger.warning(f'【{cookie_id}】迁移临时登录 profile 失败（不影响登录结果）: {exc}')


def _migrate_legacy(root: str, target: str, safe_id: str, cookie_id: str) -> None:
    """把老的 slider_<id> / user_<id> 目录搬到统一路径。"""
    if os.path.isdir(target) and os.listdir(target):
        return

    for legacy_name in (f'slider_{safe_id}', f'user_{safe_id}'):
        legacy = os.path.join(root, legacy_name)
        if not os.path.isdir(legacy):
            continue

        # 有单例锁说明浏览器正在用它，这时候搬会搬坏，等下一次
        if os.path.exists(os.path.join(legacy, 'SingletonLock')):
            logger.info(
                f'【{cookie_id}】{legacy_name} 正在被浏览器占用，暂不迁移 profile'
            )
            continue

        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(legacy, target)
            logger.info(
                f'【{cookie_id}】浏览器 profile 已迁移: {legacy_name} -> account_{safe_id}'
            )
            return
        except Exception as exc:
            logger.warning(
                f'【{cookie_id}】迁移 profile {legacy_name} 失败，改用新目录: {exc}'
            )


def clean_singleton_lock_files(profile_path: str, label: str = '') -> None:
    """清理持久化 profile 里的单例锁与崩溃标记。

    浏览器被强杀（看门狗超时、容器重启、任务被取消）后，profile 里会留下
    SingletonLock 等锁文件，以及 "exit_type": "Crashed" 的崩溃标记。下次启动时
    Chrome 会认为上次异常退出：轻则弹「未正确关闭」的恢复气泡挡住页面，
    重则新实例拿不到单例锁而行为异常 —— 表现为页面能找到元素、一操作就卡死。
    """
    tag = f'【{label}】' if label else ''

    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = os.path.join(profile_path, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
                logger.info(f'{tag}已清理残留锁文件: {name}')
        except Exception as exc:
            logger.warning(f'{tag}清理 {name} 失败: {exc}')

    for rel in ('Default/Preferences', 'Preferences'):
        pref_path = os.path.join(profile_path, rel)
        if not os.path.exists(pref_path):
            continue
        try:
            with open(pref_path, 'r', encoding='utf-8') as fp:
                prefs = json.load(fp)
            profile = prefs.get('profile')
            if not isinstance(profile, dict):
                continue
            if profile.get('exit_type') == 'Normal' and profile.get('exited_cleanly') is not False:
                continue
            profile['exit_type'] = 'Normal'
            profile['exited_cleanly'] = True
            with open(pref_path, 'w', encoding='utf-8') as fp:
                json.dump(prefs, fp, ensure_ascii=False)
            logger.info(f'{tag}已重置浏览器退出状态: {rel}')
        except Exception as exc:
            logger.warning(f'{tag}重置 {rel} 退出状态失败: {exc}')


def profile_process_marker(cookie_id: str) -> str:
    """用于精确匹配"这个账号的浏览器进程"的命令行特征。

    统一目录后仍然按 user_data_dir 匹配，避免误杀用户自己开的浏览器
    或其他账号的实例。
    """
    return os.path.join('browser_data', f'account_{safe_profile_name(cookie_id)}')


def launch_args(window_size: str = '1920,1080') -> list:
    """持久化上下文的通用启动参数（各流程共用，保证指纹一致）。"""
    return [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--no-first-run',
        '--start-maximized',
        f'--window-size={window_size}',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--lang=zh-CN',
        '--disable-blink-features=AutomationControlled',
        '--no-default-browser-check',
        '--disable-popup-blocking',
        '--disable-search-engine-choice-screen',
        '--password-store=basic',
        '--use-mock-keychain',
    ]


# 每个 profile 目录同一时刻只能被一个浏览器占用 —— Chromium 自己靠单例锁拦，
# 但两个流程同时去抢同一份目录时，抢不到的那个往往不会痛快报错，
# 而是拿到一个“半可用”的浏览器：页面能打开，一操作就卡死。
# 那种现象极难排查，所以进程内再上一道互斥，抢不到的直接报错说清楚。
_profile_locks: Dict[str, threading.Lock] = {}
_profile_locks_guard = threading.Lock()
PROFILE_ACQUIRE_TIMEOUT = 300


def _lock_key(cookie_id: Any) -> str:
    return safe_profile_name(cookie_id) if cookie_id else STAGING_PROFILE_NAME


def profile_lock(cookie_id: Any) -> threading.Lock:
    """取该 profile 对应的进程内互斥锁（惰性创建）。"""
    key = _lock_key(cookie_id)
    with _profile_locks_guard:
        lock = _profile_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _profile_locks[key] = lock
        return lock


def acquire_profile(cookie_id: Any, purpose: str = '浏览器任务') -> None:
    """同步占用该账号的 profile。用于启动与关闭分处两个方法的同步流程（如滑块验证）。

    取到后必须调用 release_profile 归还，否则下一次任务会一直等。
    """
    lock = profile_lock(cookie_id)
    if not lock.acquire(True, PROFILE_ACQUIRE_TIMEOUT):
        raise TimeoutError(
            f'{purpose}: 账号 {cookie_id or "新账号"} 的浏览器 profile 被其他任务占用'
            f'超过 {PROFILE_ACQUIRE_TIMEOUT} 秒。'
            '同一账号不能同时开两个浏览器（持久化目录是独占的），'
            '请等上一个任务结束。'
        )


def release_profile(cookie_id: Any, purpose: str = '浏览器任务') -> None:
    """归还 acquire_profile 取得的占用。重复调用会被忽略。"""
    try:
        profile_lock(cookie_id).release()
    except RuntimeError:
        # 归还次数多于取用次数，说明配对有误；记录但不影响主流程
        logger.warning(f'{purpose}: 浏览器 profile 占用重复归还，已忽略')


@contextmanager
def hold_profile(cookie_id: Any, purpose: str = '浏览器任务'):
    """同步上下文：本代码块内独占该账号的 profile。"""
    acquire_profile(cookie_id, purpose)
    try:
        yield
    finally:
        release_profile(cookie_id, purpose)


async def acquire_profile_async(cookie_id: Any, purpose: str = '浏览器任务') -> None:
    """异步占用该账号的 profile（在线程里等锁，不阻塞事件循环）。

    取到后必须调用 release_profile_async 归还。
    """
    lock = profile_lock(cookie_id)
    acquired = await asyncio.to_thread(lock.acquire, True, PROFILE_ACQUIRE_TIMEOUT)
    if not acquired:
        raise TimeoutError(
            f'{purpose}: 账号 {cookie_id or "新账号"} 的浏览器 profile 被其他任务占用'
            f'超过 {PROFILE_ACQUIRE_TIMEOUT} 秒。'
            '同一账号不能同时开两个浏览器（持久化目录是独占的），请等上一个任务结束。'
        )


async def release_profile_async(cookie_id: Any, purpose: str = '浏览器任务') -> None:
    """归还 acquire_profile_async 取得的占用。重复调用会被忽略。"""
    try:
        profile_lock(cookie_id).release()
    except RuntimeError:
        logger.warning(f'{purpose}: 浏览器 profile 占用重复归还，已忽略')


async def launch_shared_context(
    playwright: Any,
    profile_path: str,
    headless: bool = True,
    purpose: str = '浏览器任务',
) -> Any:
    """用指定 profile 启动持久化上下文：优先系统正式版 Chrome，失败回退内置 Chromium。

    为什么分两次试：系统正式版 Chrome 的指纹才能过阿里 nc；
    但容器 / NAS 里通常没装系统 Chrome，这时只能退回内置 Chromium ——
    仍然用同一个持久化目录，至少历史 profile 是真的。

    返回值是 browser_limit.LimitedContext（close() 时归还浏览器槽位）。
    """
    from utils import browser_limit

    options: Dict[str, Any] = {
        'headless': headless,
        'args': launch_args(),
        'locale': 'zh-CN',
        'timezone_id': 'Asia/Shanghai',
        'ignore_https_errors': False,
    }
    if not headless:
        # 有头时不要固定 viewport，否则窗口可缩放区域和真实浏览器不一致
        options['no_viewport'] = True

    try:
        context = await browser_limit.launch_persistent_context(
            playwright, profile_path, dict(options, channel='chrome'), purpose
        )
        logger.info(f'{purpose}: 已启动系统 Chrome（profile={profile_path}）')
        return context
    except Exception as exc:
        logger.warning(
            f'{purpose}: 系统 Chrome 启动失败（{type(exc).__name__}），回退内置 Chromium'
        )
        return await browser_limit.launch_persistent_context(
            playwright, profile_path, options, purpose
        )
