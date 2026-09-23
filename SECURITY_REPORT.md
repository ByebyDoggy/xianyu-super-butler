# 安全审计报告 — 闲鱼超级管家 (xianyu-super-butler)

- **审计对象**：`Mxucc/xianyu-super-butler`（本地仓库，`main` 分支）
- **审计方式**：白盒代码审计 + 本地搭建运行环境 + 动态漏洞验证（PoC）
- **审计范围**：后端 FastAPI 服务（`reply_server.py`、`api_captcha_remote.py`）、数据访问层（`db_manager.py`）、刮刮乐远程控制、静态/前端资源、Docker 部署配置
- **修复分支**：`security/fix-vulnerabilities`
- **修复状态**：本次报告中的 **高危/中危问题已全部修复**，并附带自动化回归测试

---

## 1. 审计环境与复现方式

本机环境：

| 组件 | 版本 |
| --- | --- |
| Python | 3.13（虚拟环境 `.venv-test`） |
| Node.js | v24.11.1（`utils/xianyu_utils.py` 需要 JS 运行时） |
| 关键依赖 | fastapi / uvicorn / pydantic / loguru / playwright / pandas / Pillow / pytest |
| 数据库 | SQLite（测试使用独立 `DB_PATH`，不触碰真实数据） |

复现命令：

```bash
# 1. 安装最小运行依赖
python -m venv .venv-test
.venv-test/Scripts/pip install fastapi uvicorn pydantic loguru websockets aiohttp \
    requests httpx PyYAML Pillow "qrcode[pil]" PyJWT passlib cryptography pandas \
    openpyxl python-multipart email-validator openai psutil pytest playwright \
    blackboxprotobuf PyExecJS

# 2. 运行安全回归测试（本次修复的验证，11 项全部通过）
.venv-test/Scripts/python -m pytest tests/security -q

# 3. 静态扫描（可选）
.venv-sec/Scripts/bandit -r . -x ./.venv-test,./frontend,./static
```

动态验证使用 FastAPI `TestClient`（`tests/security/test_security_regressions.py`），覆盖越权、SQL 注入、未授权访问、XSS、口令/会话等场景，不依赖真实闲鱼账号或外网。

---

## 2. 漏洞汇总

| ID | 漏洞 | 严重级别 | 位置 | 状态 |
| --- | --- | --- | --- | --- |
| V-01 | 备份导入列名 SQL 注入（任意数据库读取 → 可窃取管理员口令哈希） | **严重** | `db_manager.import_backup` / `POST /backup/import` | ✅ 已修复 |
| V-02 | 普通用户可写入全局系统设置（越权/提权） | **高** | `db_manager.import_backup` / `POST /backup/import` | ✅ 已修复 |
| V-03 | 卡券接口越权修改/删除（IDOR） | **高** | `PUT/DELETE /cards/{id}`、`PUT /cards/{id}/image` | ✅ 已修复 |
| V-04 | 刮刮乐远程控制 API 完全未授权（截图/鼠标控制/会话枚举） | **高** | `api_captcha_remote.py` 全部 `/api/captcha/*` 与 WebSocket | ✅ 已修复 |
| V-05 | 控制页面反射型 XSS（session_id 直接拼接进 `<script>`） | **高** | `api_captcha_remote.py` `/api/captcha/control/{session_id}` | ✅ 已修复 |
| V-06 | 内置硬编码默认 API 秘钥 `xianyu_qq_reply_2024` | **高** | `db_manager.py` 默认系统设置 | ✅ 已修复 |
| V-07 | 无盐 SHA-256 口令哈希（可彩虹表/暴力破解） | **高** | `db_manager.create_user/verify_user_password/update_user_password` | ✅ 已修复 |
| V-08 | 会话 Cookie 永久缺少 `Secure` 标记 | **中** | `reply_server.py` 登录接口 | ✅ 已修复 |
| V-09 | 登录/注册/验证码接口无限流（暴力破解、邮件轰炸） | **中** | `reply_server.py` | ✅ 已修复 |
| V-10 | 源码中提交真实闲鱼 Session Cookie | **中** | `utils/refresh_util.py` | ✅ 已修复 |
| V-11 | 敏感信息（口令哈希、Cookie、API 秘钥）写入 SQL 日志 | **中** | `db_manager._log_sql` | ✅ 已修复 |
| V-12 | 登录时在日志中明文打印 API 秘钥；秘钥比较非恒定时间 | **中** | `reply_server.verify_api_key` / `/send-message` | ✅ 已修复 |
| V-13 | 混淆 + `exec` 动态执行代码（不可审计，供应链风险） | **中** | `secure_confirm_ultra.py`、`secure_freeshipping_ultra.py` | ✅ 已删除 |
| V-14 | 验证码使用非密码学安全随机数（可预测） | **中** | `db_manager.generate_verification_code/generate_captcha` | ✅ 已修复 |
| V-15 | 容器以 root 运行；Compose 内置弱默认口令/密钥 | **中** | `Dockerfile`、`Dockerfile-cn`、`docker-compose*.yml` | ✅ 已修复 |
| V-16 | 管理员数据浏览接口返回口令哈希等敏感列 | **低** | `db_manager.get_table_data` | ✅ 已修复（脱敏 + 表名强校验） |
| V-17 | HTTPException 被 `except Exception` 吞并转 500（错误处理缺陷） | **低** | `reply_server.py` 多处 | ✅ 已修复 |
| V-18 | 任意登录用户可修改全局系统设置（越权/提权） | **高** | `PUT /system-settings/{key}` | ✅ 已修复 |
| V-19 | 任意登录用户可读取系统设置（含 AI Key / SMTP 密码 / API 秘钥） | **高** | `GET /system-settings` | ✅ 已修复 |

---

## 3. 重点漏洞详情与验证

### V-01 备份导入列名 SQL 注入（严重）

**成因**：`POST /backup/import` 接收用户上传的 JSON，`db_manager.import_backup` 直接使用其中的 `columns` 列表拼接 INSERT 语句：

```python
columns = table_data['columns']                     # 完全来自用户输入
placeholders = ','.join(['?' for _ in columns])
cursor.executemany(f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})", rows)
```

表名有白名单，但 **列名没有校验**，可闭合括号注入 SQL 表达式。

**利用 PoC（普通用户 alice 窃取 admin 口令哈希）**：

```json
{
  "data": {
    "cards": {
      "columns": ["name", "type",
        "text_content) VALUES (?, 'text', (SELECT password_hash FROM users WHERE username='admin')) --"],
      "rows": [["leaked-admin-hash"]]
    }
  }
}
```

执行后生成：

```sql
INSERT INTO cards (name,type,text_content) VALUES (?, 'text',
  (SELECT password_hash FROM users WHERE username='admin')) --) VALUES (?,?)
```

攻击者随后通过 `GET /cards` 即可读回 admin 的口令哈希。结合 V-07（无盐 SHA-256），可直接离线破解并接管管理员账号。

**修复**：
- 表名白名单 + `_validate_table_name()` 校验真实存在；
- 列名必须存在于目标表真实列（`PRAGMA table_info`）、不得重复；
- 行宽必须与列数一致，参数仍使用占位符；
- 用户级导入强制 `user_id`，并校验 `cookie_id` 归属；
- 用户级导入禁止写入 `system_settings`、`notification_channels` 等全局表。

### V-02 普通用户越权写入系统设置（高）

同上路径，原实现将 `system_settings` 纳入可导入表且用户级导入会写入，普通用户可篡改全局配置（如注册开关等）。修复后用户级导入不再允许任何全局表。

### V-03 卡券 IDOR（高）

`PUT /cards/{id}`、`DELETE /cards/{id}` 仅要求“已登录”（`require_auth`），未校验归属，`db_manager.update_card/delete_card` 也无 `user_id` 过滤，任意登录用户可修改/删除他人卡券。

**修复**：接口改用 `get_current_user`，`db_manager.update_card/delete_card` 增加 `user_id` 条件；`update_card_with_image` 同步加固。

### V-04 / V-05 刮刮乐远程控制未授权 + 反射型 XSS（高）

`/api/captcha/*` 全部接口与 `/api/captcha/ws/{session_id}` 此前没有任何鉴权，可未登录查看他人浏览器验证截图、注入鼠标事件、枚举/关闭会话。

控制页还存在反射型 XSS：

```python
html_content.replace('</body>', f'<script>window.INITIAL_SESSION_ID = "{session_id}";</script></body>')
```

`session_id` 未编码，`GET /api/captcha/control/x"><img src=x onerror=alert(1)>` 可执行任意 JS。

**修复**：
- 会话创建时生成 `access_token`，随控制 URL 下发；
- 会话级接口/WS 仅接受“正确 access_token”或“管理员登录”；
- `session_id`/`token` 用 `json.dumps` + `< > &` 转义后注入，杜绝 XSS；
- 控制页与 WS URL 携带并校验 token，保留“手机扫码远程控制”的原有使用方式。

### V-06 硬编码默认 API 秘钥（高）

`db_manager.py` 初始化 `qq_reply_secret_key = 'xianyu_qq_reply_2024'`，该值公开在源码中，任何人可调用 `/send-message` 以任意账号发送消息。

**修复**：
- 默认值改为空，首次初始化/升级时用 `secrets.token_urlsafe(32)` 生成随机秘钥；
- 检测到历史默认值时自动轮换并记录告警；
- `/send-message` 不再打印秘钥，秘钥比较改为 `hmac.compare_digest`。

### V-07 无盐 SHA-256 口令哈希（高）

```python
password_hash = hashlib.sha256(password.encode()).hexdigest()
```

**修复**：改为 PBKDF2-HMAC-SHA256（600,000 次迭代 + 16 字节随机盐，格式 `pbkdf2_sha256$iter$salt$hash`）；旧哈希在用户下次成功登录时**透明升级**，不影响存量数据。

### V-08 ~ V-17

- **V-08**：Cookie `secure=` 依据 `SESSION_COOKIE_SECURE` 环境变量或 `X-Forwarded-Proto`/请求协议动态设置（HTTPS/反向代理下自动 `Secure`）。
- **V-09**：新增 `utils/rate_limit.py`（滑动窗口内存限流），对登录（10 次/5 分钟）、注册（5 次/小时）、邮件验证码（5 次/10 分钟）、图形验证码（30 次/分钟）限流，超限返回 429。
- **V-10**：删除 `utils/refresh_util.py` 末尾提交的真实 Session Cookie。
- **V-11**：`_log_sql` 对命中 `password/token/cookie/secret/api_key` 等字段的参数统一脱敏为 `***`。
- **V-12**：见 V-06。
- **V-13**：删除未被引用的 `secure_confirm_ultra.py`、`secure_freeshipping_ultra.py`（base64+hex+zlib+`exec` 的混淆代码；项目实际使用的是可审计的 `*_decrypted.py`）。
- **V-14**：`random` → `secrets` 生成验证码。
- **V-15**：容器新增非 root 用户 `appuser` 并以之运行；Compose 移除 `user: "0:0"` 与弱默认 `ADMIN_PASSWORD`/`JWT_SECRET_KEY`，新增 `SESSION_COOKIE_SECURE` 开关。
- **V-16**：`get_table_data` 校验表名并对 `password_hash/value/password/token/secret/api_key` 列脱敏。
- **V-17**：为卡券、备份导入等接口补 `except HTTPException: raise`，避免把 400/404 误转成 500。
- **V-18/V-19**：`GET/PUT /system-settings` 由 `require_auth` 改为 `require_admin`，普通用户不再能读取（AI Key、SMTP 密码、QQ 秘钥等）或修改全局配置；前端同步将「系统与AI」入口限制为管理员可见。
- 另外新增安全响应头（`X-Content-Type-Options: nosniff`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`），并在会话校验时复查账号是否仍启用、权限是否被撤销（撤销即时生效）。

---

## 4. 回归测试

`tests/security/test_security_regressions.py`（`pytest`，全部通过）：

```
test_idor_update_card_denied
test_idor_delete_card_denied
test_backup_import_column_sql_injection_rejected
test_backup_import_cannot_write_system_settings
test_captcha_api_requires_auth
test_captcha_control_page_xss_escaped
test_default_qq_secret_rotated
test_password_hashed_with_pbkdf2
test_login_rate_limited
test_session_cookie_secure_on_https
test_admin_data_table_sql_injection_rejected
```

结果：

```
11 passed
```

修复前，对应的动态 PoC 确认 13 项可利用；修复后复测 **0 项可利用**。

---

## 5. 残余风险与建议（未在本次强制变更）

1. **`POST /xianyu/reply` 无鉴权**：该接口仅计算并返回匹配到的回复文本，不发送消息；设计上供外部自动回复服务调用。建议后续增加 `X-API-Key` 校验或仅监听内网。当前保留以兼容既有集成。
2. **口令策略**：未强制最小长度/复杂度，建议在注册/改密处增加策略校验。
3. **`{send_user_name}` 模板格式化**：关键词回复模板由管理员/用户配置，`str.format` 仅能读取参数属性，风险有限；如需彻底收敛可改用显式占位符替换。
4. **多实例限流**：当前限流为单进程内存实现，多副本部署请替换为 Redis 等共享存储。
5. **Bandit `B608/B324` 提示**：剩余告警均为「表名/列名已强校验的内部拼接 SQL」与「闲鱼/极验协议要求使用的 MD5 签名」，非可利用漏洞；`B104`（0.0.0.0 监听）为容器部署特性。

---

## 6. 修复文件清单

| 文件 | 变更 |
| --- | --- |
| `db_manager.py` | 口令哈希升级、备份导入加固、卡券越权过滤、日志脱敏、随机秘钥、`secrets` 验证码、表名/列名校验 |
| `reply_server.py` | 登录限流、会话 Cookie Secure、安全响应头、卡券鉴权、备份导入异常处理、秘钥日志/比较、会话有效性校验、邮箱校验 |
| `api_captcha_remote.py` | 全接口鉴权、access_token 机制、XSS 修复 |
| `utils/captcha_remote_control.py` | 会话 access_token 生成/读取 |
| `utils/item_search.py` | 控制 URL 携带 access_token |
| `captcha_control.html` | WebSocket 携带 token，wss 自适应 |
| `utils/rate_limit.py` | 新增限流器 |
| `utils/refresh_util.py` | 移除真实 Cookie |
| `secure_confirm_ultra.py` / `secure_freeshipping_ultra.py` | 删除混淆 `exec` 代码 |
| `Dockerfile` / `Dockerfile-cn` / `docker-compose*.yml` | 非 root 运行、移除弱默认口令 |
| `.gitignore` | 忽略安全测试临时产物 |
| `tests/security/test_security_regressions.py` | 新增安全回归测试 |

---

## 7. 新增功能：在线更新（feature/online-update）

在本次修复基础上新增「在线更新」功能，默认绑定到自有仓库
`ByebyDoggy/xianyu-super-butler`（分支 `main`），可在系统设置中修改。

### 使用方式

1. 「系统设置 → 在线更新」填写仓库（`owner/name`）、分支，私有仓库可选填 GitHub Token；
2. 点击「保存所有配置」后点击「检查更新」，展示当前版本、远端提交与是否需要更新；
3. 点击「立即更新」执行更新（默认更新后自动重启，Docker 等由重启策略拉起）。

### 接口

| 接口 | 说明 | 权限 |
| --- | --- | --- |
| `GET /system/update/check` | 通过 GitHub API 检查最新提交 | 管理员 |
| `POST /system/update/apply` | 执行更新（`restart`/`force` 可选） | 管理员 |

### 实现与安全设计（`utils/updater.py`）

- 优先使用 `git fetch` + `git merge --ff-only`（存在 `.git` 时），失败不会强改本地未提交修改；`force=true` 时才会 `git reset --hard`；
- 非 Git 部署（如 Docker，`.git` 被 `.dockerignore` 排除）回退为下载 GitHub 源码 tarball 并覆盖项目文件；
- **来源限制**：仓库/分支经严格正则校验，只允许配置的 `owner/name`，无法被改成任意 URL（防 SSRF）；
- **命令注入防护**：所有子进程调用使用参数数组 + `shell=False`，并对分支名做白名单字符校验；
- **归档安全**：解压前拒绝路径穿越、符号链接/硬链接/设备文件，防止写到项目目录之外；
- **数据保护**：更新时跳过 `data/`、`logs/`、`backups/`、`static/uploads`、`global_config.yml`、`.env`、`node_modules`、虚拟环境等，不会覆盖运行数据与配置；
- **鉴权**：接口仅管理员可用；Token 不写日志（并命中 SQL 日志脱敏规则）；
- **可回滚**：Git 部署下为快进合并，保留完整提交历史，可随时 `git reset` 回退。

#### Docker 下的持久化更新（重要）

容器内程序文件位于可写层，容器重建/镜像重拉后会回退，因此 Docker 下采用“镜像级更新”：

- 镜像构建时通过 `--build-arg GIT_SHA` 注入提交号（`ENV APP_COMMIT`），使容器内无需 `.git` 也能比对版本；
- `docker-compose` 默认使用 GHCR 镜像并 `pull_policy: always`；启用 `auto-update` profile 后由 Watchtower 负责拉镜像+重建容器；
- `apply_update` 检测到容器环境且配置了 `WATCHTOWER_URL` 时，仅调用其 HTTP API 触发镜像更新（`persistent=True`）；Watchtower 不可达时回退为容器内更新并在接口/界面返回 `persistent=False` 与明确警告；
- Watchtower HTTP API 通过 `WATCHTOWER_TOKEN` 鉴权，仅在 compose 内网暴露；挂载 `docker.sock` 属高权限操作，作为可选 profile 提供。

> 注意：在线更新会用仓库中的代码覆盖程序文件，这本质上是一次受信任的代码分发，
> 请确保该仓库由你可控；建议在更新前通过「检查更新」确认提交来源。

### 相关测试

`tests/security/test_security_regressions.py` 新增：
`test_system_settings_requires_admin`、`test_update_endpoints_require_admin`、
`test_update_repo_validation`、`test_update_protected_paths`、
`test_check_update_rejects_bad_repo_without_network`、
`test_docker_detection`、`test_local_commit_falls_back_to_app_commit`、
`test_apply_update_uses_watchtower_in_docker`、
`test_apply_update_docker_without_watchtower_marks_not_persistent`、
`test_trigger_watchtower_http_error`、`test_trigger_watchtower_unreachable_returns_none`。
