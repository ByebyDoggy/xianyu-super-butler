"""轻量级内存速率限制器。

用于登录、注册、发送验证码等敏感接口的暴力破解/滥用防护。
单进程内存实现，适合本项目单实例部署；如需多实例部署请替换为 Redis 等共享存储。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class SlidingWindowRateLimiter:
    """基于滑动窗口的内存限流器（线程安全）。"""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, bucket: str, key: str) -> bool:
        """记录一次请求；未超限返回 True，超限返回 False。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits[(bucket, key)]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            return True

    def remaining(self, bucket: str, key: str) -> int:
        """返回剩余可用次数。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get((bucket, key))
            if not q:
                return self.max_requests
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            return max(0, self.max_requests - len(q))

    def reset(self, bucket: str, key: str) -> None:
        """清空某个 key 的计数（例如登录成功后）。"""
        with self._lock:
            self._hits.pop((bucket, key), None)


# 登录：同一来源 5 分钟内最多 10 次尝试
login_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=300)
# 注册：同一来源 1 小时内最多 5 次
register_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=3600)
# 邮件验证码：同一来源 10 分钟内最多 5 次
email_code_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=600)
# 图形验证码/极验：同一来源 1 分钟内最多 30 次
captcha_limiter = SlidingWindowRateLimiter(max_requests=30, window_seconds=60)


def client_ip(request) -> str:
    """尽量获取真实客户端 IP（仅在可信代理后使用 X-Forwarded-For）。"""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
    except Exception:
        pass
    return "unknown"
