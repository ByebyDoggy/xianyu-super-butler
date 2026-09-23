# 安全审计报告 — 23Star/xianyu-super-butler

- **审计对象**：`23Star/xianyu-super-butler`
- **采用基线**：`80a7d50`（2026-09-13，**最新的可运行提交**）
- **审计方式**：白盒代码审计 + 静态扫描（Bandit）+ 本地动态 PoC 复现
- **结论**：发现并修复 **1 严重 + 2 高危**；同时修复了一个导致服务不可用的上游缺陷。

---

## 0. 重要：上游 `main` 当前不可运行

审计前先做可用性校验，发现上游 `main`（`b376973`）缺少 4 个被 `app/reply_server.py` 直接 import 的模块，
容器启动时 Web 服务会直接崩溃（实测 `uvicorn服务器启动失败: No module named 'app.delivery_template'`）：

| 缺失模块 | 原因 |
| --- | --- |
| `app/delivery_template.py` | 从未提交（`39184ba` 引入 import） |
| `app/routers/logistics_agent.py` | 从未提交 |
| `app/routers/logistics_quote.py` | 从未提交 |
| `app/services/notification_test.py` | 被 `.gitignore` 的 `*_test.py` 规则误排除 |

这 4 个 import 均由同一个提交 `39184ba`（`feat: improve buyer automation and notification rules`，2026-09-13）引入。
因此本仓库改用该提交的父提交 **`80a7d50`** —— 最新的、import 全部可解析的版本（比 `v3.1.0` 新约一个月）。

---

## 1. 漏洞汇总

| ID | 漏洞 | 级别 | 位置 | 状态 |
| --- | --- | --- | --- | --- |
| **U-01** | `/backup/import` 列名 SQL 注入（任意登录用户可读取任意数据库数据，含管理员口令哈希） | **严重** | `app/db_manager.py` `import_backup` | ✅ 已修复 |
| **U-02** | 用户级备份导入可写全局 `system_settings` / `notification_channels`（越权改全局配置） | **高** | 同上 | ✅ 已修复 |
| **U-03** | 口令使用无盐 SHA-256 哈希 | **高** | `app/db_manager.py` 口令相关方法 | ✅ 已修复 |
| **U-04** | 未配置 `ADMIN_PASSWORD` 时使用默认口令 `admin123` | **中** | `app/db_manager.py` 初始化 | ⚠️ 已告警，建议强制修改 |
| **U-05** | Dockerfile 只安装 playwright 的 Chromium，未安装 patchright（滑块链路）的 Chromium，导致滑块验证必然失败 | **中** | `Dockerfile` | ✅ 已修复 |

---

## 2. 重点漏洞详情

### U-01 严重：备份导入列名 SQL 注入

`POST /backup/import` 仅要求登录（任意注册用户即可）。`import_backup` 直接使用上传 JSON 的 `columns` 拼接 SQL：

```python
columns = table_data['columns']          # 完全来自用户输入
placeholders = ','.join(['?' for _ in columns])
cursor.executemany(f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})", rows)
```

表名有白名单，**列名未做任何校验**，可闭合括号注入任意 SQL 表达式。

**动态 PoC（已复现）**：普通用户 `attacker` 构造：

```json
{"data":{"cards":{"columns":["name","type",
  "text_content) VALUES (?, 'text', (SELECT password_hash FROM users WHERE username='victim')) --"],
  "rows":[["leaked-card"]]}}}
```

生成 SQL：

```sql
INSERT INTO cards (name,type,text_content) VALUES (?, 'text',
  (SELECT password_hash FROM users WHERE username='victim')) --) VALUES (?,?,?)
```

复现结果：

```
leaked card: ('leaked-card', 'text', '9eac1f11...8491c', 1)
victim hash: 9eac1f11...8491c
LEAK SUCCESS: True
```

结合 U-03（无盐哈希），可直接离线爆破管理员口令并接管系统。

**修复**：表名白名单 + 必须真实存在；列名必须存在于目标表（`PRAGMA table_info`）且不重复；行宽校验；用户级导入禁止全局表、强制 `user_id`、校验 `cookie_id` 归属。

**修复后复测**：

```
hash prefix: pbkdf2_sha256$
exploit ok: False | leaked: False | cards: []
legit import: True
```

### U-02 高：用户级导入越权写全局表

原实现将 `system_settings`、`notification_channels` 纳入用户级可导入表。修复后用户级白名单已排除这两张表。

### U-03 高：无盐 SHA-256 口令哈希

`create_user` / `verify_user_password` / `update_user_password` 及默认 admin 初始化均使用 `hashlib.sha256(password)`。

**修复**：PBKDF2-HMAC-SHA256（600,000 次迭代 + 16 字节随机盐，格式 `pbkdf2_sha256$iter$salt$hash`）；兼容历史 SHA-256 并在登录成功后透明升级。

### U-05 中：Dockerfile 缺少 patchright 的 Chromium

- `playwright==1.60.0` → Chromium **1223**（已安装）；
- `patchright>=1.52.0`（解析为 1.63）→ Chromium **1243**（未安装）。

滑块链路使用 patchright，导致运行时 `BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium-1243/...`，滑块验证必然失败。
**修复**：Dockerfile 安装完 playwright 的 Chromium 后再执行 `patchright install chromium`。

---

## 3. 静态扫描说明

Bandit 告警均为 LOW/MEDIUM 且经复核非可利用：B608 为表名/列名拼接 SQL（表名来自固定列表或白名单，列名来自固定字段，U-01 修复后唯一用户可控入口已封堵）；B324 为闲鱼/极验协议要求的 MD5；B310 位于开发脚本；B104/B108/B603 属正常。未发现 `eval/exec/pickle.load/yaml.load/shell=True`、硬编码真实凭证、CORS 通配等问题。

---

## 4. 修复文件

| 文件 | 变更 |
| --- | --- |
| `app/db_manager.py` | PBKDF2 口令哈希与透明升级；`import_backup` 表/列/行校验与用户级隔离 |
| `Dockerfile` | 增加 `patchright install chromium`，补齐滑块所需 Chromium |

---

## 5. 验证

```bash
# 本地动态验证（修复前后对比）
python sec_verify.py
# 修复前: LEAK SUCCESS: True
# 修复后: exploit ok: False | leaked: False | legit import: True
```
