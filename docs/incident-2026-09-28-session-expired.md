# 2026-09-28 登录态过期导致「自动发货」静默漏发

## 现象

三个订单卡在「待发货」：卡密已经发给买家，但平台侧一直没被标记为已发货。
买家拿到卡、平台认为卖家没发货，卖家还面临超时处罚。

```
3316465670228005079  买家 3788679694
3317077920214000476  买家 2210779228788
3316900873229009465  买家 375506306
```

## 直接原因：两套通道、两套凭据，一套死了另一套还活着

|  | 自动回复 / 发卡密 | 自动发货（标记已发货） |
|---|---|---|
| 走哪条路 | IM **WebSocket 长连接** | mtop **HTTP 接口**（`mtop.taobao.idle.logistic.consign.dummy`） |
| 用什么凭据 | 建连时换来的 `accessToken`，连接期间一直复用 | **每个请求都带 Cookie 登录态**，服务端每次重新校验 |
| 当时状态 | ✅ 活着（咬 16:13 换来的旧 token） | ❌ 登录态失效 → `FAIL_SYS_SESSION_EXPIRED` |

所以表现是「消息照收、AI 照回、卡密照发，但发货就是不成功」。实测用当前 Cookie
打**任何** mtop 接口（确认发货 / 卖家订单 / IM 取 token）都返回 `SESSION_EXPIRED`，
连伪造令牌和不带 Cookie 也是同一个错误 —— 即"未通过登录鉴权"。

会话失效时间：约 16:33（北京时间 23:33）。16:13 的 Cookie 校验还全通过，16:36 起全失败。

## 核心问题：四个「本该喊出来」的地方全都静默了

这才是这次漏发三个订单、拖了几小时才被发现的原因。

| # | 沉默点 | 为什么没响 |
|---|---|---|
| 1 | **界面显示绿灯「监听中」** | 徽章来自 `connection_state`，它只表示 IM 长连接。而终态标志 `needs_relogin` 只在 `refresh_token()` 内部置位，那个刷新被「收到消息后 5 分钟冷却」**无限期推迟** —— 店里一直有人说话就永远不执行。越活跃的账号越查不出来（漏检的恰好是最忙的主账号） |
| 2 | **Cookie 校验失败这条路径不置位** | 17:05:46 其实已经检测到 `Cookie验证失败`，但只打了 warning + 发通知，界面状态没更新 |
| 3 | **通知被"正常过期"过滤器吞掉** | `_is_normal_token_expiry` 把含 `Session过期` 的消息当成"正常、不用通知"直接 return —— 方向刚好反了：会话过期是终态，只能重新扫码 |
| 4 | **发货超时兜底告警静默失效** | `_check_delivery_timeout` 依赖卖家端接口，接口挂了却只在 `logger.debug` 记一行。于是「查不到超时订单」和「真的没有超时订单」在日志和界面上完全一样 |

另外 `auto_confirm` 对 `SESSION_EXPIRED` 也递归重试 4 次，每次结果一样（纯浪费）。
「卡密已发但平台未确认发货」的告警其实发出去了（ServerChan 有记录），但措辞里
既没有订单号也没说要重新登录，容易被当成噪音划过去。

## 修复

| 提交 | 内容 |
|---|---|
| `53ac36c` | 新增 `_mark_session_expired()` 统一标记（界面转「需重新扫码」+ 通知一次）；新增 `_cooldown_blocks_session_check()` 给冷却加硬上限（推迟超过 `interval + 900s` 必须放行）；Cookie 校验失败路径接入标记；确认发货遇会话过期立即停手；发货超时检查失败提到 WARNING + 单独告警；「卡密已发未确认发货」告警带订单号和处置建议 |
| `a41e460` | `_is_normal_token_expiry` 不再吞掉会话过期（只保留可自愈的令牌过期）；未配置账号密码时的通知改成可操作的措辞，并与 `_mark_session_expired` 共用 `need_relogin` 冷却窗口 |

新增测试：`tests/test_session_expiry_visibility.py`
（标记语义与去重、冷却硬上限的四个边界、会话过期不重试且标记账号、
普通错误仍重试、会话过期必须通知而令牌过期仍静默）。

## 必须人工做的（代码救不回来）

**重新扫码登录**是唯一恢复方式。会话过期无法用刷 Cookie / 密码登录补救：

- 刷 Cookie 只是轮换设备指纹（`sca/cbc/atpsida/tfstk`），浏览器带的也是同一份失效 Cookie，校验必然还是失败；
- 这三个账号都没配用户名/密码，`_try_password_login_refresh` 直接跳过。

登录后还要处理已经卡住的订单（平台侧仍是待发货）。

## 自查命令

```bash
# 卡密已发但平台未确认发货的单
sqlite3 data/xianyu_data.db \
  "SELECT order_id,buyer_id,order_status,system_shipped,created_at FROM orders
   WHERE system_shipped=1 AND order_status NOT IN ('shipped','completed','cancelled');"

# 会话是否还活着（用当前 Cookie 直接打 mtop 只读接口）
python -c "import asyncio;from utils.xianyu_seller_api import XianyuSellerAPI;..."

# 账号运行态（前端徽章来源）：running / need_relogin / connecting / failed
curl -s localhost:8080/cookies/details -H "Authorization: Bearer <token>"
```

## 经验

1. **「能收发消息」不等于「账号可用」**。IM 长连接和 mtop 鉴权是两条独立的命，
   长连接靠旧 token 能苟很久，掩盖了登录态已死。
2. **静默降级比直接崩溃危险**。这次没有任何异常抛出，日志里全是"正常"的心跳，
   四个本该报警的地方全都只是"少打了一行日志"。
3. **限流/冷却机制要有兜底上限**。用来"错峰"的冷却如果能把故障检测无限期推迟，
   就会在最需要它的活跃账号上失效。
4. **终态与可自愈状态必须分开**：`令牌过期` 会自愈（下次请求带新令牌），
   `会话过期` 只能人工介入 —— 两者混在一个过滤器里，就必然把需要人的那个也吞掉。
