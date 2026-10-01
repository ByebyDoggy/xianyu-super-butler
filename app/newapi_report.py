"""闲鱼发货 → new-api 对账上报。

发货成功后把「订单 → 卡密」映射 POST 到 new-api（/api/xianyu/shipments），
打通「闲鱼订单 → 兑换码 → 平台用户」链路，供补偿发放与退货校验使用。

失败时写入本地待上报队列（xianyu_report_queue 表），由后台任务重试。
new-api 不可达不阻塞发货主流程。
"""

import logging
import re
import sqlite3
import threading
import time

import requests

logger = logging.getLogger(__name__)

# 面向 new-api 的上报配置：从 global_config.yml 的 NEW_API_REPORT 段读取。
#   base_url:   例如 https://ai.lesec.top
#   token:      new-api 系统访问令牌（RootAuth）
_REPORT_CFG = {
    "base_url": "",
    "token": "",
}
_cfg_lock = threading.Lock()

# 卡密行形态：<32位兑换码>\t¥金额 或 纯 32 位码。
_CODE_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])")


def configure(base_url: str, token: str) -> None:
    """由启动流程（读 global_config.yml 的 NEW_API_REPORT 段）调用。"""
    with _cfg_lock:
        _REPORT_CFG["base_url"] = (base_url or "").rstrip("/")
        _REPORT_CFG["token"] = token or ""


def _ensure_queue_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS xianyu_report_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            buyer_id TEXT DEFAULT '',
            item_id TEXT DEFAULT '',
            amount TEXT DEFAULT '0',
            card_key TEXT DEFAULT '',
            card_amount TEXT DEFAULT '',
            shipped_at INTEGER NOT NULL,
            attempts INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        )
        """
    )


def extract_card_key(delivery_content: str) -> tuple:
    """从发货内容中提取 (兑换码, 面额标注)。

    支持形态：
      - ``358c...ef\\t¥10``   → ("358c...ef", "¥10")
      - ``358c...ef``          → ("358c...ef", "")
    找不到 32 位码时返回 ("", "")（非兑换码卡密只归档不上报关联）。
    """
    if not delivery_content:
        return "", ""
    m = _CODE_RE.search(delivery_content)
    if not m:
        return "", ""
    key = m.group(0)
    # 面额：紧随其后的 \t¥xx 标注
    tail = delivery_content[m.end():]
    amount = ""
    amt_match = re.match(r"\s*[¥￥]\s*([0-9.]+)", tail)
    if amt_match:
        amount = f"¥{amt_match.group(1)}"
    return key, amount


def report_shipment(order_id, buyer_id, item_id, amount, delivery_content, db_conn=None, shipped_at=None):
    """发货成功后调用。同步尝试上报，失败则入队重试（不抛异常）。"""
    if not order_id:
        return
    card_key, card_amount = extract_card_key(delivery_content)
    if not card_key:
        # 非兑换码形态（文本卡密等）不入 new-api 关联链路。
        return
    shipped_at = shipped_at or int(time.time())
    payload = {
        "order_id": str(order_id),
        "buyer_id": str(buyer_id or ""),
        "item_id": str(item_id or ""),
        "amount": str(amount or "0"),
        "card_key": card_key,
        "card_amount": card_amount,
        "shipped_at": shipped_at,
    }
    try:
        if _post_shipment(payload):
            return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[new-api上报] 直报失败，入队: {e}")
    _enqueue(payload, db_conn)


def _post_shipment(payload: dict) -> bool:
    with _cfg_lock:
        base, token = _REPORT_CFG["base_url"], _REPORT_CFG["token"]
    if not base or not token:
        return False
    resp = requests.post(
        f"{base}/api/xianyu/shipments",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code == 200 and data.get("success"):
        return True
    raise RuntimeError(f"new-api rejected: {resp.status_code} {str(data)[:120]}")


def _enqueue(payload: dict, db_conn=None) -> None:
    try:
        conn = db_conn
        close = False
        if conn is None:
            from app.db_manager import db_manager

            conn = sqlite3.connect("data/xianyu_data.db", timeout=10)
            close = True
        try:
            _ensure_queue_table(conn)
            conn.execute(
                "INSERT INTO xianyu_report_queue (order_id,buyer_id,item_id,amount,card_key,card_amount,shipped_at,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    payload["order_id"], payload["buyer_id"], payload["item_id"], payload["amount"],
                    payload["card_key"], payload["card_amount"], payload["shipped_at"], int(time.time()),
                ),
            )
            conn.commit()
        finally:
            if close:
                conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"[new-api上报] 入队失败（数据丢失）: {e}")


def flush_queue(db_conn=None, limit: int = 50) -> int:
    """重试待上报队列。返回成功条数。"""
    with _cfg_lock:
        base, token = _REPORT_CFG["base_url"], _REPORT_CFG["token"]
    if not base or not token:
        return 0
    conn = db_conn
    close = False
    if conn is None:
        conn = sqlite3.connect("data/xianyu_data.db", timeout=10)
        close = True
    flushed = 0
    try:
        _ensure_queue_table(conn)
        rows = conn.execute(
            "SELECT id, order_id, buyer_id, item_id, amount, card_key, card_amount, shipped_at"
            " FROM xianyu_report_queue ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        for row in rows:
            rid, order_id, buyer_id, item_id, amount, card_key, card_amount, shipped_at = row
            payload = {
                "order_id": order_id, "buyer_id": buyer_id, "item_id": item_id,
                "amount": amount, "card_key": card_key, "card_amount": card_amount,
                "shipped_at": shipped_at,
            }
            try:
                if _post_shipment(payload):
                    conn.execute("DELETE FROM xianyu_report_queue WHERE id = ?", (rid,))
                    conn.commit()
                    flushed += 1
            except Exception as e:  # noqa: BLE001
                conn.execute(
                    "UPDATE xianyu_report_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                    (str(e)[:200], rid),
                )
                conn.commit()
    finally:
        if close:
            conn.close()
    return flushed


def start_flush_worker(interval_seconds: int = 300) -> None:
    """启动后台重试线程（幂等，可多次调用）。"""

    def _worker():
        while True:
            try:
                n = flush_queue()
                if n:
                    logger.info(f"[new-api上报] 队列补报 {n} 条")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[new-api上报] 队列重试异常: {e}")
            time.sleep(interval_seconds)

    t = threading.Thread(target=_worker, daemon=True, name="newapi-ship-report")
    t.start()
