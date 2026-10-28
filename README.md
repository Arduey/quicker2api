# API 分发平台

把 Quicker 的「推送服务（长连接）」包装成对外的 HTTP 接口：管理员在这里定义接口，
平台用户用 API Key 调用，系统把用户填的参数拼成 JSON 推给 Quicker 运行动作，并把动作返回的数据带回给用户。
按积分计费（调用前预扣，失败自动退回）。

---

## 一、宝塔部署（全程可视化，不需要命令行）

### 1. 上传代码

有两种方式，任选其一。

**方式 A：FTP 直接同步（推荐，不需要解压）**

项目根目录自带 `deploy.py`，走 FTP 把文件树直接传上去，**只传有变化的文件**：

```bash
# 第一次先写连接信息（.deploy.env 已在 .gitignore 里，不会被提交）
cat > .deploy.env <<'EOF'
FTP_HOST=你的服务器IP
FTP_PORT=21
FTP_USER=FTP账号
FTP_PASS=FTP密码
EOF

python deploy.py --dry-run   # 先看会传哪些
python deploy.py             # 真正上传
python deploy.py --all       # 强制全量重传
python deploy.py --delete    # 顺带删掉远端多余文件
```

传完在宝塔「Python 项目」点一次 **重启** 即可，**不需要打包、不需要解压**。

> 也可以直接用 GitHub 拉取：在宝塔终端或「计划任务」里
> `git pull` 到项目目录，同样只需重启。

**方式 B：宝塔文件管理器上传**

宝塔面板 → **文件** → 进入 `/www/wwwroot/` → 新建目录（例如 `toolbox`）→ 进入该目录 →
把 `app/`、`templates/`、`static/` 三个目录和 `wsgi.py`、`app.py`、`requirements.txt`
逐个上传（或用「上传文件夹」一次传整个目录）。

无论哪种方式，最终目录结构应是这样（`wsgi.py` 必须和 `app/`、`templates/`、`static/` 同级）：

```
/www/wwwroot/toolbox/
├── wsgi.py             ← gunicorn 用这个（wsgi:app）
├── app.py              ← 本地调试用（python app.py）
├── requirements.txt
├── app/
├── templates/
└── static/
```

> 不要上传 `config.json`、`install.lock`、`__pycache__`、`gunicorn_conf.py` 这些：
> 它们由安装向导或宝塔自己生成，覆盖掉会出问题。`deploy.py` 已自动排除。

### 2. 创建 MySQL 数据库

宝塔面板 → **数据库** → **添加数据库**：

| 项目 | 填什么 |
|---|---|
| 数据库名 | 例如 `toolbox_api` |
| 用户名 | 例如 `toolbox_api` |
| 密码 | 点「随机」生成，**记下来** |
| 访问权限 | 本地服务器 |

> 数据库留空也行，安装向导会自动建库（前提是填的账号有建库权限）。

### 3. 建 Python 项目

宝塔面板 → **网站** → **Python 项目** → **添加 Python 项目**：

| 项目 | 填什么 |
|---|---|
| 项目名称 | `toolbox` |
| 项目路径 | `/www/wwwroot/toolbox` |
| Python 版本 | **3.8 或更高**（推荐 3.10 / 3.11 / 3.12） |
| 框架 | Flask |
| 启动方式 | **gunicorn** |
| 应用名称 / 启动命令 | `wsgi:app`（`app:app` 也兼容，见下方说明） |
| 通讯协议 | `wsgi` |
| 端口 | 例如 `8000`（不冲突即可） |
| 运行用户 | `www` |
| 是否安装依赖 | **是**（会自动读 `requirements.txt`） |

> ⚠️ **应用名称填 `wsgi:app`（推荐）。**
> 项目里同时有 `app.py` 和 `app/` 包，直接写 `app:app` 时 Python 会优先把
> `app` 解析成同名包，历史版本会报
> `Failed to find attribute 'app' in 'app'` / `AttributeError`。
> 现在包里也挂了一份应用实例，所以 `app:app` 和 `wsgi:app` 都能启动，
> 但 `wsgi:app` 语义更明确，建议用它。

如果没有「自动安装依赖」选项，创建好项目后在项目列表点 **模块** / **依赖** →
**从 requirements.txt 安装**，粘贴下面内容或者直接指向文件：

```
Flask>=3.0,<4.0
PyMySQL>=1.1.0
cryptography>=42.0
gunicorn>=21.2.0
```

> `gunicorn` 只在 Linux 上需要；宝塔是 Linux，所以需要。
> 如果宝塔提示找不到 gunicorn，就在同一界面把启动方式改成 **python** 并填 `app.py`（也能跑，只是单进程）。

### 4. 绑定域名（可选但推荐）

Python 项目 → **域名管理** → 添加你的域名 → 开启 **SSL**（Let's Encrypt 一键申请）。

> 不走域名也可以先用 `http://服务器IP:8000` 访问，但正式使用时建议绑定域名 + HTTPS，
> 否则 API Key 会以明文在网络里传输。

### 5. 跑安装向导

浏览器打开 `http://你的域名/install`，四步走完：

1. **环境检查** —— 全绿后下一步（缺依赖会告诉你去哪装）
2. **数据库** —— 填第 2 步记下的库名/用户名/密码，点「测试连接」
3. **站点与邮件** —— 站点名称、SMTP（QQ 邮箱用**授权码**不是登录密码），点「发送测试邮件」确认能收到
4. **管理员** —— 填管理员邮箱和密码，点「开始安装」

安装完成后会自动跳到 `/login`，用刚才设的管理员邮箱密码登录，会自动进入管理控制台。

> 安装过程会自己建表、建管理员、生成 `config.json` 和 `install.lock`，
> **不需要手动创建或修改任何文件**。
> 想重装：用文件管理器删掉 `install.lock` 再访问 `/install`（不会清空已有用户和日志）。

---

## 二、配置 Quicker

### 1. Quicker 端

1. Quicker 面板 → 点头像 → **推送服务** → **连接到推送服务**
2. 头像菜单 → 打开会员中心的 **推送设置** → 生成 **推送验证码**，记下它
3. 准备一个要被执行的动作，动作最后用 **「停止」模块** 输出要返回的内容
   （否则 `wait=true` 时拿不到返回值）

### 2. 平台后台

后台 → **全局设置** → **Quicker 推送参数**，填：

| 参数 | 说明 |
|---|---|
| `toUser` | Quicker 账号邮箱 |
| `code` | 上面生成的推送验证码 |
| `toDevice` | 目标设备。留空=当前活动电脑；`*`=所有电脑；`!电脑名`=排除该电脑；填电脑名=只发该电脑 |
| `operation` | 一般选 `action`（运行动作） |

点 **获取已连接设备** 可以从下拉里直接选设备名；点 **测试推送** 会往剪贴板推一段文字来验证连通性。

### 3. 创建 API

后台 → **API 管理** → **新建 API**：

- **接口名称 / 调用标识(slug)**：用户看到的名字，以及调用地址 `/api/v1/<slug>`
- **每次调用消耗积分**：0 表示免费
- **action**：Quicker 动作名称或动作 ID（留空则不指定）
- **wait**：默认 `true`（等动作返回结果）
- **maxWaitMs**：默认 `30000`（接口上限 30 秒）
- **txt**：默认 `false`（返回 JSON）
- **data 参数**：右侧「+ 添加参数」加键值，每个键可设显示名、提示、是否必填
- 右侧实时预览最终推给 Quicker 的完整 JSON

用户调用时填的值会被组成**单层、压缩、不换行**的 JSON 文本作为 `data` 传给 Quicker：

```
{"title":"周报","content":"今天完成了…"}
```

---

## 三、用户调用方式

### 1. 拿 API Key

用户注册（邮箱验证码）→ 登录 → **账号设置** 里能看到自己的 API Key，可随时重置。

### 2. 调接口

```bash
curl -X POST https://你的域名/api/v1/<slug> \
  -H "Authorization: Bearer tb_xxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"title":"周报","content":"今天完成了三件事"}'
```

也可以用 `X-API-Key: tb_xxx` 头，或者用邮箱密码换 JWT：

```bash
curl -X POST https://你的域名/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"你的密码"}'
# → {"ok":true,"token":"eyJ...","expires_in":604800}
```

然后 `Authorization: Bearer <token>`。

### 3. 返回格式

成功：

```json
{
  "ok": true,
  "data": {"isSuccess": true, "successCount": 1, "timeCost": 219, "devices": {"PC-1": "动作返回的内容"}},
  "cost": 2,
  "points": 128,
  "time_cost_ms": 230
}
```

失败（**积分自动退回**）：

```json
{
  "ok": false,
  "error": {"code": "push_failed", "message": "等待 Quicker 返回结果超时（maxWaitMs=30000）"},
  "cost": 0,
  "refunded": true,
  "points": 128
}
```

积分不足时 HTTP 状态是 `402`，`error.code` 为 `insufficient_points`。

其他可用端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/me` | 当前账号信息与余额 |
| GET | `/api/v1/apis` | 我可用接口清单（含参数定义） |
| GET | `/api/v1/apis/<slug>` | 单个接口的参数定义 |

---

## 四、登录与权限

**管理员和平台用户共用同一个登录入口 `/login`**：填同一个邮箱密码表单，系统先按普通用户校验、
再按管理员校验，谁匹配就进谁的界面。

| 身份 | 登录后落点 | 能看到什么 |
|---|---|---|
| 平台用户 | `/u` 用户中心 | 自己的积分、API Key、调用记录、卡密兑换 |
| 管理员 | `/admin` 管理控制台 | API 管理、用户管理、卡密、日志、全局设置 |

管理员**也能进入用户中心**（侧栏有「用户中心 / 管理控制台」互跳），但那是**管理视角**：

- 概览显示全站统计，不显示个人积分
- 「可用接口」每张卡片右上角多一个 **`编辑`** 按钮，点它直接跳到后台编辑该接口
- 「调用记录」变为**全站日志**，可查看所有人的调用
- 没有 API Key、不能卡密兑换、不能在线调试（管理员没有积分账户）

> 管理员如要测试接口调用，请用普通账号。
> 旧的 `/admin/login` 地址仍保留（会重定向到 `/login`），书签不会失效。

## 五、后台功能

| 菜单 | 作用 |
|---|---|
| 概览 | 调用量、用户数、积分消耗，Quicker/SMTP 配置健康提示 |
| API 管理 | 增删改接口、上下线、data 参数编辑器、实时预览 |
| 用户管理 | 搜索、加分/扣分、禁用/启用、重置 API Key |
| 卡密充值 | 批量生成卡密、导出 txt、查看批次与使用情况 |
| 调用日志 | 每次调用的参数（code 已打码）、Quicker 返回、耗时、扣费、失败原因 |
| 全局设置 | Quicker 四个全局参数、SMTP、站点名称、新用户赠送积分 |

---

## 六、常见问题

**Q：注册收不到验证码？**
后台 → 全局设置 → 邮箱发信，点「发送测试邮件」。
QQ 邮箱必须用**授权码**（设置 → 账户 → 开启 SMTP 服务后生成），不是 QQ 密码；
若返回 `Connection refused`，换 465 端口（SSL）。

**Q：调用返回「平台尚未配置 Quicker 的 toUser / code」**
去后台全局设置把 `toUser` 和 `code` 填上并保存。

**Q：返回「等待 Quicker 返回结果超时」**
1. Quicker 里确认已连接推送服务、设备在线（绿点）
2. 动作里要用「停止」模块输出返回值
3. 适当调大该 API 的 `maxWaitMs`（上限 30000）

**Q：改了代码不生效？**
宝塔 Python 项目 → 该项目 → **重启**。

**Q：`toDevice` 怎么填？**
留空发给当前活动电脑；`*` 发给账号下所有电脑；`!电脑名` 发给除这台以外的电脑；
填具体电脑名只发那一台（必须已建立长连接）。

**Q：日志会存多久？**
没有自动清理。数据量大时可在宝塔 phpMyAdmin / 数据库管理里定期删 `api_logs` 旧记录。

---

## 七、目录说明

```
wsgi.py               WSGI 入口（gunicorn 指向 wsgi:app）★宝塔用这个
app.py                本地调试入口（python app.py）
requirements.txt      依赖清单
app/
  __init__.py         应用工厂、请求钩子、错误处理
  config.py           config.json / install.lock 读写
  db.py               MySQL 访问层（连接复用、事务、建表）
  schema.sql          建表 SQL（安装时自动执行）
  security.py         密码哈希、JWT、登录态、API Key
  mailer.py           SMTP 发信 + 邮箱验证码
  quicker.py          Quicker 推送对接、data 组装、字段校验
  util.py             时间/校验/CSRF/settings 辅助
  routes/
    install.py        安装向导
    auth.py           注册/登录/验证码/JWT
    manage.py         管理后台
    portal.py         用户中心
    api_v1.py         对外 API
templates/            页面模板
static/               css / js
config.json           安装后自动生成（数据库密码、密钥）
install.lock          安装后自动生成（安装锁）
```

> `config.json` 里有数据库密码和签名密钥，**不要提交到代码仓库**，也不要发给别人。
