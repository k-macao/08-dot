# 章鱼AI.行情速报

> A股/港股/美股指数行情 → Dot 硬件设备（Quote/0 等）墨水屏卡片，卡片标题：**章鱼AI.行情速报**
>
> 交易时段**逐小时**推送最新点位：卡上只有三组指数 —— **A股**（上证/深成/创业板/沪深300）、**港股**（恒指/恒生国企/恒生科技）、**美股**（道琼斯/纳斯达克/标普500），共 10 项，不带其它内容。
>
> 两条实现路线（可并存）：
>
> | 路线 | 说明 | 入口 |
> | --- | --- | --- |
> | **① 纯固件 BIN（刷机）** | ESP-IDF 独立固件：设备自己联网取**每日占星运势**、16px 中文字库本地渲染、每天 07:00 自动更新，不依赖 Dot 云（保持原样） | [`firmware/`](firmware/README.md) + CI 产物 `zhangyu-daily-bin` |
> | **② 云端 Text API（免刷机）** | 交易时段每小时取指数行情推到设备（**本文**） | 下方流水线 + GitHub Actions `daily-push` |

> 固件路线可以在**不接硬件**的情况下自检排版：
> `python3 firmware/tools/preview_card.py` 会把卡片渲染成 PNG，
> `python3 firmware/tools/gen_font.py --check` 检查字库缺字；
> 已修的显示问题清单见 [firmware/README.md](firmware/README.md#排版显示问题回归清单)。

```text
[东方财富 push2]  ─┐
[新浪财经 hq.sinajs] ─┼─►  zhangyu 排版/幂等/交易时段门控  ──►  Dot 云 Text API  ──►  设备墨水屏
[本地缓存 data/]  ──┘          run_daily.py push                /api/authV2/open/        Quote/0 152×296
                                    ▲                          device/:id/text
                     GitHub Actions cron：工作日交易时段逐小时
                     （UTC 01:30–08:30 = 京/澳 09:30–16:30；UTC 13:30–21:30 = 美股盘中）
```

## 一、对 dot_open_platform 的理解（任务 1）

仓库 [MindReset/dot_open_platform](https://github.com/MindReset/dot_open_platform) 是 MindReset Dot 系列设备的**开放硬件资源仓库**，包含：

| 目录 | 内容 |
| --- | --- |
| `devices/quote_0/` | Quote/0（ESP32-C3 + 152×296 墨水屏）外壳 STEP/STL、GPIO 引脚表、显示驱动/波形笔记、ESP-IDF 示例、开发文档 |
| `devices/read_0/` | Read Pico 外壳与结构件，官方固件仓 [MindReset/read_pico_firmware](https://github.com/MindReset/read_pico_firmware) |
| `capabilities/rand_0_display_mode/` | Rand/0 本地 Display Mode：WebSocket 协议参考 + 浏览器发图示例 |
| `manifest.json` 等 | 机器可读元数据、LICENSE/AUTHORS/MAINTAINERS |

仓库同时指向**线上开发者平台**（<https://dot.mindreset.tech/docs/service/open>），这才是“按接口推送内容”的入口：

| 接口 | 用途 |
| --- | --- |
| **Text API** `POST /api/authV2/open/device/:deviceId/text` | **向设备推送文本卡片（本项目使用）** |
| Image API / Canvas API | 推送图片 / 结构化画布内容 |
| Device Status / Task List / Settings | 查询设备状态、内容列表、设备设置 |
| 频控 | 10 请求/秒 |

**Text API 关键点（本项目的实现依据）**：

- 认证：`Authorization: Bearer dot_app_{API Key}`，API Key 在 Dot. App 中创建导出；
- 正文字段：`title`（标题）、`message`（正文，保留 `\n` 换行）、`signature`（签名栏），可选 `icon` / `link`(NFC) / `styles`(字体)；
- 列表选择：`taskType: "loop"|"fixed"`（默认 loop），`taskKey` 定位列表内具体项；**前提是已在 Dot. App → Content Studio 把 “Text API” 内容加入设备的循环/固定列表**；
- `refreshNow: true` 可让在线设备立即刷新循环内容（固定列表项只能按 App 里配置的时间点刷新）；
- 状态码：`200` 成功；`404`＝设备不存在或列表里没有 Text API 项；`403`＝无权限；`500`＝设备切换失败。

> 本项目即基于 Text API v2 端点（旧端点 `/api/open/text` 已弃用但仍转发）。

## 二、指数行情数据源（任务 2）

调研对比后选定（均免密钥）：

| 优先级 | 数据源 | 语言 | 说明 |
| --- | --- | --- | --- |
| 1（主用） | 东方财富 `push2.eastmoney.com/api/qt/ulist.np/get` | JSON | 一次批量取回全部 10 项（`secids` 拼接，`fltt=2`）；`push2` / `82.push2` / `72.push2` 三个镜像依次降级 |
| 2（备用） | 新浪 `hq.sinajs.cn/list=…` | GBK 文本 | 独立解析（A股/港股/美股三种字段布局）；需带 `Referer: https://finance.sina.com.cn/` |
| 3（兜底） | `data/quotes_cache.json` | JSON | 上一次成功取数的本地缓存，展示时标注 `缓存(MM-DD HH:MM)`，**不编数据** |

要点：

- **东财 secid 实测口径**（参考本系列仓库 k-macao/001 的踩坑记录）：恒生科技 = `124.HSTECH`（`100.HSTECH` 已死，混入批量会让整批返回 `data:null`）；`100.NDX` 返回的是纳斯达克**综合**指数（与 ^IXIC 同值）；
- 东财批量失败（含单个失效 secid 拖垮整批）自动换新浪；两边都取到部分指数时合并显示（宁可少几项也不整卡缺失）；
- 指数清单与代码对照随时可查：`python3 run_daily.py indices`。

## 三、每小时推送流水线（任务 3）

一次 `python3 run_daily.py push` 完成：

1. **定时触发**（见下文「交易时段定时」）或手动执行；
2. `--trading-only`（定时任务专用）：先判交易时段 —— 京/澳工作日 09:30–17:30 为 A股/港股盘中，美东工作日 09:30–17:30 为美股盘中（含夏令时）；休市直接跳过（退出码 0）；
3. 按数据源优先级取**最新 10 项指数点位**；
4. 排版为 Text API 卡片（`title` / `message` / `signature`，签名取推送时刻）；
5. 幂等检查：`data/state.json` 记录 `last_push_at`，**50 分钟内**已推送则跳过（对应整点节奏，`--force` 穿透；旧的按日格式记录不拦）；
6. `POST …/device/{id}/text`（带重试与 Key 换形），成功后写状态文件并打印卡片。

### 卡片效果

运行 `python3 run_daily.py preview --fixture tests/fixtures/quotes_em.json` 可离线查看卡片（测试夹具数据）：

```text
==============================================
标题：章鱼AI.行情速报
----------------------------------------------
A股
上证 3888.12 +0.56%
深成 13456.78 -0.31%
创业板 3123.45 +1.02%
沪深300 4567.89 +0.44%
港股
恒指 24567.89 +0.31%
恒生国企 8765.43 +0.28%
恒生科技 5678.90 -0.66%
美股
道琼斯 47123.45 -0.12%
纳斯达克 19876.54 +0.25%
标普500 5890.12 +0.18%
----------------------------------------------
签名：2026年11月10日 星期二 16:05 · 章鱼AI
数据源：东方财富（10 项）
==============================================
```

> 三组小节 + 每项一行（`短名 点位 涨跌幅`），点位保留 2 位小数、涨跌幅始终带符号；
> 实际点位随行内换行显示，设备可自动折行。未取到的指数只从卡上缺席，不写假数据。

## 快速开始

### 0. 环境

- Python **3.9+**（纯标准库，零第三方依赖）

### 1. 准备设备侧（一次性）

1. Dot. App → 创建并**导出 API Key**（形如 `dot_app_xxx`，应用不保存，务必存好）；
2. Dot. App → 获取**设备序列号**（device serial number）；
3. Dot. App → Content Studio → 把 **Text API** 内容加入设备的**循环列表**（或固定列表）——否则接口返回 `404`；
4. 设备联网、通电。

### 2. 配置

```bash
cp config.example.json config.json    # 已在 .gitignore 中，不会提交
# 编辑 config.json：device_id 等
export DOT_API_KEY='dot_app_xxxxxxxxx'   # API Key 只走环境变量
```

### 3. 验证

```bash
python3 run_daily.py doctor                 # 环境自检（含行情源与交易时段）
python3 run_daily.py preview                # 预览行情卡片（不推送）
python3 run_daily.py push --dry-run         # 走完整流水线但不调用 Dot 云
python3 run_daily.py push                   # ✅ 真实推送到设备
```

常用参数：

```bash
python3 run_daily.py push --trading-only    # 休市时跳过（定时任务用）
python3 run_daily.py push --force           # 忽略 50 分钟幂等，强制重推
python3 run_daily.py push --source sina     # 只用新浪源（默认 auto=东财→新浪→缓存）
python3 run_daily.py push --source em       # 只用东财源
python3 run_daily.py push --no-title        # 卡片不带标题（config title:"" 同效）
python3 run_daily.py push --show-payload    # 打印请求体（凭据缺失时也会打印）
python3 run_daily.py push --dry-run --show-payload   # 只看排版+请求体，无需任何凭据
python3 run_daily.py preview --fixture tests/fixtures/quotes_em.json  # 离线夹具预览
python3 run_daily.py indices                # 查看 10 项指数清单（分组/secid/新浪代码）
python3 run_daily.py doctor --check-key     # 真调只读接口验证 API Key 是否有效
```

## 交易时段定时（任务 3 的“每小时”）

### 方式 A：GitHub Actions（推荐，仓库已在 GitHub 上）

工作流：[.github/workflows/daily-push.yml](.github/workflows/daily-push.yml)

cron：`30 1-8,13-21 * * 1-5`（**工作日**、每小时第 30 分钟）：

| 窗口（UTC） | 本地时间 | 覆盖 |
| --- | --- | --- |
| 01:30–08:30 | 京/澳 09:30–16:30 | A股/港股盘中逐小时 |
| 13:30–21:30 | 美东 09:30–17:30（夏）/ 08:30–16:30（冬） | 美股盘中逐小时 |

定时触发带 `--trading-only`：**休市（盘前/收盘/周末/节假日）的触发由脚本判为非交易时段并直接跳过（exit 0，run 保持绿色）**；夏令时切换、cron 自身延迟都由脚本的时段判断兜底，宁可宽一点。

手动 **Run workflow** 不加门控 —— 随时可以推送/预览，方便验证。

一次性配置（仓库 **Settings → Secrets and variables → Actions**）：

| 名称 | 类型 | 值 |
| --- | --- | --- |
| `DOT_API_KEY` | **Secret**（只放 Secret） | Dot. App 导出的 API Key，形如 `dot_app_xxx` |
| `DOT_DEVICE_ID` | Secret **或** Variable | 设备序列号（工作流两处都会读，放错页面也能跑） |
| `ZHANGYU_TITLE` | 可选 Variable/环境 | 覆盖卡片标题 |
| `ZHANGYU_SOURCE` | 可选 | `em` / `sina` / `auto`，覆盖数据源 |

> 工作流第一步是 **Preflight（凭据自检）**：凭据缺失会立刻以
> `::error::` 注释 + Job Summary 给出配置位置，而不是等到推送时才报一句
> 「未配置设备序列号」。手动触发时勾选 **dry_run** 可以只预览卡片与请求体
> （不需要任何凭据）。

**排障：`错误：未配置 device_id` / `Error: Process completed with exit code 2`**

1. `Settings → Secrets and variables → Actions` 里新建 `DOT_DEVICE_ID`
   （Secret 或 Variable 均可），值 = 设备序列号；`DOT_API_KEY` 必须是 Secret；
2. 只想先看输出：手动 Run workflow 时勾选 `dry_run`，或本地
   `python run_daily.py push --dry-run --show-payload`；
3. 本地 `python run_daily.py doctor --require-credentials` 与 CI 的预检等价，
   退出码 2 表示凭据不全（默认 `doctor` 不会因为缺凭据而失败）。

### API Key 到底长什么样（别再靠长度猜）

官方 Text API 文档的示例是 `Authorization: Bearer dot_app_<64 位>`（共 72 字符），
但**真实 Key 并不保证是这个形状**：本项目实测过一个 68 位、没有 `dot_app_` 前缀的值，
补前缀会被 400 拒、而**原值却能通过认证**（返回 404 而非 400，说明 Key 是好的）。

所以：

| 情况 | 处理 |
| --- | --- |
| 带 `dot_app_` 前缀 | 直接用 |
| 只有 64 位载荷 | 自动补前缀 |
| 其它长度、无前缀 **但服务端认** | 自动换形重试，成功后会**记住**这个形态（写进 `data/state.json` 的 `key_form`），下次直接用它，不再先吃一个 400 |
| 以 `ghp_` / `sk-` / `xox*-` / `secret_` / `eyJ` 开头 | 这是**别的服务**的 token，一定会失败，`doctor` 会直接点出来 |

形态与文档不符时，日志只作提示（`提示（不影响使用…）`），不再当错误喊 —— 是否有效由服务端说了算。
想强制指定形态：`export ZHANGYU_KEY_FORM=raw`（或 `prefixed`）。

> **决定性自查**：Dot. App → **API Key** 列表会显示每个 Key 的「创建时间 / 最近使用时间」。
> 刚跑完 CI，如果那个 Key 的「最近使用时间」没有更新，说明请求里带的不是它。

### 404「没有文本 API 内容」——必须先在 App 里加一次

`POST …/text` 只更新**已存在**的 Text API 槽位。设备列表里没有这个槽位时，无论 Key 多正确都会 404：

```
Dot. App → 内容工坊 / Content Studio → 选择这台设备
        → 把「Text API」内容加入【循环列表】→ 保存
```

加一次即可，之后每次自动推送。手动排查：

```bash
python3 run_daily.py doctor --check-content
# - loop 列表: TEXT_API(txt-loop-1) ✅ 有 Text API 内容
# - fixed 列表: 空 ❌ 没有 Text API 内容
```

本工具还会在推送前自动查两个列表：**循环列表里没有、固定列表里有 → 自动改用固定列表推送**（并在日志里说明），
省掉一次必然失败的 404。

**排障：`HTTP 400 This API key is invalid. Check the key and try again.`**

请求已经发出去了，是 Dot 云判定 **Key 本身无效**（不是权限、也不是设备问题）。按顺序核对：

0. **先看日志报的是哪种形态**：本工具会依次尝试「补前缀」与「原值」，两者都被 400 才判定 Key 无效；
   只有日志出现「以其它服务的 token 开头」时才说明贴错了东西（`ghp_` / `sk-` / `eyJ` 等）；
1. **重新导出一遍**：Dot. App → 导出 API Key，重新生成通常会让旧 Key **立即作废**；
2. **别带污染**：复制时把引号、空格、`Bearer ` 一起带进 Secret 是最常见的坑
   （本工具会就地清理并打印「已清理 引号/Bearer 前缀」，但仍建议原样粘贴）；
3. **别填反**：`DOT_DEVICE_ID` = 设备序列号（如 `ABCD1234ABCD`），
   `DOT_API_KEY` = `dot_app_…`；填反时 `doctor` 会直接告警「两个 Secret 可能填反了」；
4. **位置要对**：`DOT_API_KEY` 必须是 **Secret**（Variables 页读不到）；
5. **本地复现**：`python run_daily.py doctor --check-key`
   —— 真的拿 Key 去问一次 Dot 云只读接口，会给出「✅ 接受 / ❌ Key 无效 / 404 设备不存在 / 403 无权限」。

`doctor` 打印的「Key 指纹」只包含**长度与结构特征**（是否带 `dot_app_` 前缀、清理了什么），
永远不会回显密钥内容。

推送时若「补前缀」形态被判 400/401，会自动用**原值**再试一次（有些面板导出的值已自带
前缀规则），成功时会打印「服务端接受的是原值形态」——这样"其实只差个前缀"不会再被误判成 Key 失效。

### 方式 B：本地 crontab

```cron
# 京/澳工作日 09:30–16:30 每半小时触发一次，由 --trading-only 决定发不发
30 9-16 * * 1-5 cd /path/to/08-dot && DOT_API_KEY='dot_app_xxx' /usr/bin/python3 run_daily.py push --trading-only >> logs/push.log 2>&1
```

> macOS/Linux 通用；确保 cron 环境能访问外网，并提前 `mkdir -p logs`。
> 美股时段（美东盘中 = 京/澳 21:30–05:30）如需覆盖，再加一行对应小时即可，
> `--trading-only` 会按美东时间自行判断。

## 配置项（config.json）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `device_id` | `""` | 设备序列号；环境变量 `DOT_DEVICE_ID` 优先 |
| `title` | `章鱼AI.行情速报` | 卡片标题；设为 `""`（或 CLI 传 `--no-title`）时卡片不带标题，Text API 也不发 `title` 字段 |
| `brand` | `章鱼AI` | 签名栏署名 |
| `source` | `auto` | `auto`（东财→新浪→缓存）/ `em` / `sina`；环境变量 `ZHANGYU_SOURCE` 与 CLI `--source` 覆盖 |
| `fixture` | `null` | 离线夹具路径（东财原始响应或归一化 JSON），设置后不联网 |
| `task_type` | `loop` | `loop` 循环列表 / `fixed` 固定列表 |
| `task_key` | `null` | fixed 列表项 key（配合 `--task-key`） |
| `task_alias` | 同标题 | 设备任务列表中的显示名 |
| `refresh_now` | `true` | 在线设备立即刷新循环内容 |
| `timezone` | `Asia/Macau` | 卡片签名所用时区 |
| `state_file` | `data/state.json` | 幂等状态（`last_push_at`、`key_form` 等） |
| `cache_file` | `data/quotes_cache.json` | 行情缓存（两边数据源全挂时兜底） |
| `icon_url` / `nfc_link` | `null` | 可选：40×40 图标 URL / NFC 跳转链接 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

单测覆盖：指数清单与东财/新浪解析（含部分响应、坏响应、空行情行跳过）、降级链与缓存兜底、交易时段门控（京/澳与美东盘中、午界、周末、夏令时）、三组卡片排版与签名、CLI 契约（凭据缺失、`--show-payload` / `--dry-run` 语义、占位 device_id、50 分钟幂等与 `--force`、`--trading-only` 门控、`--no-title`），Dot 客户端请求形态（本地 mock HTTP 服务，校验路径/鉴权/请求体）与错误码处理，API Key 清洗（引号/空白/`Bearer ` 前缀）、Key 形态软校验与补前缀失败自动换形重试、Key 形态记忆、列表查询与内容槽位自动选择（loop/fixed），以及无效 Key 的排查提示；固件 NVS 配置测试照旧。全部测试离线可跑（行情夹具 + mock 云）。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `404` | 设备序列号不对，或设备列表里还没添加 **Text API** 内容（Dot. App → Content Studio）；`fixed` 模式需 `task_key` 与列表匹配 |
| `400 This API key is invalid` | Key 本身无效：重新导出（别带引号/空格）、确认没和 `DOT_DEVICE_ID` 填反；`python3 run_daily.py doctor --check-key` 可复现 |
| `403` | 该 API Key 无权操作此设备（换绑/重建 Key） |
| `500` | 设备侧切换失败（设备离线/未响应），脚本会自动重试 3 次 |
| 推了但屏幕没变 | 循环内容需设备在线且 `refreshNow:true`；**固定列表**项只在 App 设定的刷新点更新 |
| 50 分钟内想再推 | `python3 run_daily.py push --force` |
| 定时跑显示「非交易时段…跳过」 | 正常：休市门控放行（run 为绿色）；盘中仍被跳过则多半是节假日休市 |
| `行情获取失败` | 东财/新浪都挂且本地无缓存；重跑一次（脚本会自动降级）。缓存兜底时卡上标注 `缓存(MM-DD HH:MM)` |
| 某项指数缺席卡片 | 该指数没取到（如单个 secid 失效被过滤）；只显示真实数据，不编造 |
| CI 报 `未配置 device_id`（exit 2） | 仓库没配 `DOT_DEVICE_ID`（Secret 或 Variable 均可）；见上文「排障」 |
| `config.json` 里还是模板值 | `YOUR_DEVICE_SERIAL_NUMBER` 会被当成“未配置”，不会再打到 Dot 云换一个 404 |
| 想看请求体但没凭据 | `push --show-payload` 现在会先打印请求体再报错；`--dry-run --show-payload` 直接成功退出 |
| 密钥安全 | API Key 只放环境变量/CI Secret；`config.json`、`data/` 已被 `.gitignore` 排除 |

## 项目结构

```text
08-dot/
├── README.md                      # 本文档
├── config.example.json            # 配置模板（复制为 config.json）
├── run_daily.py                   # 入口：python3 run_daily.py <cmd>
├── zhangyu/
│   ├── quotes.py                  # 行情数据源：东财 → 新浪 → 缓存 + 交易时段门控
│   ├── formatter.py               # Text API 卡片排版（三组指数 title/message/signature）
│   ├── dot_client.py              # Dot Text API 客户端（认证/重试/错误码）
│   └── cli.py                     # push / preview / indices / doctor
├── .github/workflows/daily-push.yml  # 交易时段逐小时推送（cron 30 1-8,13-21 * * 1-5）
├── tests/                         # 单测 + 行情/接口 fixture（全部离线）
├── requirements.txt               # 零依赖说明
└── .gitignore                     # 排除 config.json / data/ / logs/
```

## 参考链接

- Dot Open Platform 仓库：<https://github.com/MindReset/dot_open_platform>
- 开发者平台文档：<https://dot.mindreset.tech/docs/service/open>
- Text API：<https://dot.mindreset.tech/docs/service/open/text_api>

## License

MIT（跟随本仓库）。行情数据仅供参考，不构成投资建议。
