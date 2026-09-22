"""安全回归测试。

覆盖本次安全修复的关键漏洞，防止回归：
- 卡券接口越权（IDOR）
- /backup/import 列名 SQL 注入与系统设置越权写入
- 刮刮乐远程控制接口未授权访问与反射型 XSS
- 默认 QQ 回复秘钥、密码哈希、登录限流、HTTPS 会话 Cookie

运行：
    pytest tests/security -q
"""
import hashlib
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 必须在导入 reply_server / db_manager 之前指定独立数据库
_TMP_DIR = tempfile.mkdtemp(prefix="xianyu_sec_test_")
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

import reply_server  # noqa: E402
from db_manager import db_manager  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(reply_server.app)


@pytest.fixture(scope="module", autouse=True)
def _seed_users():
    assert db_manager.create_user("alice", "alice@example.com", "Password123!")
    assert db_manager.create_user("bob", "bob@example.com", "Password123!")
    assert db_manager.create_user("admin", "admin@example.com", "AdminPass123!")
    with db_manager.lock:
        cursor = db_manager.conn.cursor()
        cursor.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
        db_manager.conn.commit()
    yield


def _login(username: str, password: str) -> TestClient:
    c = TestClient(reply_server.app)
    resp = c.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture(scope="module")
def alice(_seed_users):
    return _login("alice", "Password123!")


@pytest.fixture(scope="module")
def bob(_seed_users):
    return _login("bob", "Password123!")


def test_idor_update_card_denied(alice, bob):
    alice_user = db_manager.get_user_by_username("alice")
    card_id = db_manager.create_card(name="alice-card", card_type="text",
                                     text_content="secret", user_id=alice_user["id"])
    resp = bob.put(f"/cards/{card_id}", json={"name": "hacked"})
    assert resp.status_code in (403, 404)
    assert db_manager.get_card_by_id(card_id)["name"] == "alice-card"


def test_idor_delete_card_denied(alice, bob):
    alice_user = db_manager.get_user_by_username("alice")
    card_id = db_manager.create_card(name="alice-card2", card_type="text",
                                     text_content="secret", user_id=alice_user["id"])
    resp = bob.delete(f"/cards/{card_id}")
    assert resp.status_code in (403, 404)
    assert db_manager.get_card_by_id(card_id) is not None


def test_backup_import_column_sql_injection_rejected(alice):
    alice_user = db_manager.get_user_by_username("alice")
    victim_hash = db_manager.get_user_by_username("admin")["password_hash"]
    columns = ["name", "type",
               "text_content) VALUES (?, 'text', (SELECT password_hash FROM users WHERE username='admin')) --"]
    payload = {"data": {"cards": {"columns": columns, "rows": [["leaked"]]}}}
    resp = alice.post("/backup/import",
                      files={"file": ("backup.json", json.dumps(payload), "application/json")})
    assert resp.status_code == 400
    cards = db_manager.get_all_cards(alice_user["id"]) or []
    assert all(c["text_content"] != victim_hash for c in cards)


def test_backup_import_cannot_write_system_settings(alice):
    payload = {"data": {"system_settings": {"columns": ["key", "value", "description"],
                                            "rows": [["pwned_setting", "pwned", "x"]]}}}
    alice.post("/backup/import",
               files={"file": ("backup.json", json.dumps(payload), "application/json")})
    assert db_manager.get_system_setting("pwned_setting") is None


def test_captcha_api_requires_auth(client):
    assert client.get("/api/captcha/sessions").status_code == 401
    assert client.get("/api/captcha/status/whatever").status_code == 403
    assert client.delete("/api/captcha/session/whatever").status_code == 403
    assert client.post("/api/captcha/check_completion", json={"session_id": "x"}).status_code == 403


def test_captcha_control_page_xss_escaped(client):
    admin = _login("admin", "AdminPass123!")
    path = '/api/captcha/control/x%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E'
    resp = admin.get(path)
    assert resp.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in resp.text


def test_default_qq_secret_rotated(client):
    assert reply_server.verify_api_key("xianyu_qq_reply_2024") is False
    secret = db_manager.get_system_setting("qq_reply_secret_key")
    assert secret and secret != "xianyu_qq_reply_2024"


def test_password_hashed_with_pbkdf2(_seed_users):
    stored = db_manager.get_user_by_username("alice")["password_hash"]
    assert stored.startswith("pbkdf2_sha256$")
    assert stored != hashlib.sha256(b"Password123!").hexdigest()
    assert db_manager.verify_user_password("alice", "Password123!")
    assert not db_manager.verify_user_password("alice", "wrong")


def test_login_rate_limited(client):
    from utils.rate_limit import login_limiter
    try:
        statuses = set()
        for _ in range(20):
            statuses.add(client.post("/login", json={"username": "alice", "password": "bad"}).status_code)
        assert 429 in statuses
    finally:
        login_limiter.reset("login", "testclient")


def test_session_cookie_secure_on_https(client):
    resp = client.post("/login", json={"username": "alice", "password": "Password123!"},
                       headers={"X-Forwarded-Proto": "https"})
    assert "secure" in resp.headers.get("set-cookie", "").lower()


def test_admin_data_table_sql_injection_rejected(client):
    admin = _login("admin", "AdminPass123!")
    resp = admin.get("/admin/data/users%20UNION%20SELECT%201")
    assert resp.status_code in (400, 404)
