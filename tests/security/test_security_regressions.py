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


# ---------------------------------------------------------------------------
# 在线更新功能相关安全测试
# ---------------------------------------------------------------------------

def test_system_settings_requires_admin(alice, bob):
    """普通用户不能读取或修改全局系统设置"""
    assert bob.get("/system-settings").status_code == 403
    assert bob.put("/system-settings/registration_enabled", json={"value": "false"}).status_code == 403
    # 管理员可以访问
    assert _login("admin", "AdminPass123!").get("/system-settings").status_code == 200


def test_update_endpoints_require_admin(bob):
    """在线更新接口仅管理员可用"""
    assert bob.get("/system/update/check").status_code == 403
    assert bob.post("/system/update/apply", json={"restart": False}).status_code == 403


def test_update_repo_validation():
    """更新仓库/分支必须符合格式，防止指向任意来源"""
    from utils import updater

    class _FakeDB:
        def __init__(self, **kw):
            self._kw = kw

        def get_system_setting(self, key):
            return self._kw.get(key)

    # 默认仓库应合法
    cfg = updater.get_update_config(_FakeDB())
    assert cfg["repo"] == updater.DEFAULT_REPO

    # 非法仓库/分支应被拒绝
    for bad in ("evil.com/repo", "../../etc/passwd", "owner", "a/b/c"):
        with pytest.raises(updater.UpdateError):
            updater.get_update_config(_FakeDB(github_repo=bad))

    for bad_branch in ("../main", "/etc", "a b"):
        with pytest.raises(updater.UpdateError):
            updater.get_update_config(_FakeDB(github_branch=bad_branch))


def test_update_protected_paths():
    """更新时不得覆盖运行数据/配置"""
    from utils import updater

    assert updater._is_protected("data/xianyu_data.db")
    assert updater._is_protected("logs/app.log")
    assert updater._is_protected("static/uploads/images/a.png")
    assert updater._is_protected("global_config.yml")
    assert updater._is_protected("frontend/node_modules/x.js")
    assert not updater._is_protected("utils/updater.py")
    assert not updater._is_protected("reply_server.py")


def test_check_update_rejects_bad_repo_without_network():
    """非法仓库应在发起网络请求前就被拒绝"""
    from utils import updater

    class _FakeDB:
        def get_system_setting(self, key):
            return {"github_repo": "not a repo"}.get(key)

    with pytest.raises(updater.UpdateError):
        updater.check_update(_FakeDB())


def test_update_copy_tree_skips_protected(tmp_path):
    """更新覆盖文件时不得触碰运行数据/配置"""
    from utils import updater
    import io
    import tarfile

    src = tmp_path / "src"
    (src / "utils").mkdir(parents=True)
    (src / "data").mkdir(parents=True)
    (src / "static" / "uploads" / "images").mkdir(parents=True)
    (src / "utils" / "a.py").write_text("x = 1", encoding="utf-8")
    (src / "data" / "db.sqlite").write_text("secret", encoding="utf-8")
    (src / "global_config.yml").write_text("cfg", encoding="utf-8")
    (src / "static" / "uploads" / "images" / "a.png").write_text("img", encoding="utf-8")

    dst = tmp_path / "dst"
    dst.mkdir()
    copied = updater._copy_update_tree(src, dst)

    assert (dst / "utils" / "a.py").exists()
    assert not (dst / "data" / "db.sqlite").exists()
    assert not (dst / "global_config.yml").exists()
    assert not (dst / "static" / "uploads" / "images" / "a.png").exists()
    assert copied == 1


def test_update_safe_extract_rejects_traversal_and_links(tmp_path):
    """归档解压必须拒绝路径穿越与链接文件"""
    from utils import updater
    import io
    import tarfile

    # 路径穿越
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../evil.txt")
        data = b"x"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        with pytest.raises(updater.UpdateError):
            updater._safe_extract_tar(tar, tmp_path / "out1")

    # 符号链接
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="repo/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        with pytest.raises(updater.UpdateError):
            updater._safe_extract_tar(tar, tmp_path / "out2")
