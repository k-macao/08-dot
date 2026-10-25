# 章鱼AI.每日占一占

> 每日占星运程 → Dot 硬件设备（Quote/0 等）墨水屏卡片，卡片标题：**章鱼AI.每日占一占**
>
> 两条实现路线（可并存）：
>
> | 路线 | 说明 | 入口 |
> | --- | --- | --- |
> | **① 纯固件 BIN（刷机）** | ESP-IDF 独立固件：设备自己联网取运势、16px 中文字库本地渲染、每天 07:00 自动更新，不依赖 Dot 云 | [`firmware/`](firmware/README.md) + CI 产物 `zhangyu-daily-bin` |
> | **② 云端 Text API（免刷机）** | 按 dot_open_platform 开放接口每日把卡片推到设备 | 本文下方流水线 + GitHub Actions `daily-push` |

> 固件路线可以在**不接硬件**的情况下自检排版：
> `python3 firmware/tools/preview_card.py` 会把卡片渲染成 PNG，
> `python3 firmware/tools/gen_font.py --check` 检查字库缺字；
> 已修的显示问题清单见 [firmware/README.md](firmware/README.md#排版显示问题回归清单)。

```text
[线上占星接口]  ──►  zhangyu 排版/降级/幂等  ──►  Dot 云 Text API  ──►  设备墨水屏
 v2.xxapi.cn ─┐          run_daily.py          /api/authV2/open/        Quote/0 152×296
 freehoroscope ─┤                                  device/:id/text
 内置离线引擎 ──┘
       ▲
  每日定时：GitHub Actions (UTC 23:00 = 京/澳 07:00)  或  本地 crontab
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

## 二、每日占星运程数据源（任务 2）

调研对比后选定（均免密钥）：

| 优先级 | 数据源 | 语言 | 说明 |
| --- | --- | --- | --- |
| 1（主用） | `https://v2.xxapi.cn/api/horoscope?type={sign}&time=today` | 中文 | 免费免密、QPS 50；返回综合/爱情/事业/财运/健康 **文本+指数+星级**、宜忌、幸运色/数字/贵人星座，内容最契合卡片 |
| 2（备用） | `https://freehoroscopeapi.com/api/v1/get-horoscope/daily?sign={sign}` | 英文 | 免费免密，仅综合运势文本 |
| 3（兜底） | `zhangyu.providers.fetch_offline` | 中文 | 内置“章鱼AI 离线占卜引擎”：按 (日期, 星座) 种子确定性生成，**任何网络故障都能出卡** |

`providers` 顺序可在配置中调整；任何一源失败自动降级到下一源（日志输出到 stderr）。

## 三、每日推送流水线（任务 3）

一次 `python3 run_daily.py push` 完成：

1. **定时触发**（见下文“每日定时”）或手动执行；
2. 决定星座：`sign_mode=random` 每日抽签（同日幂等）或 `fixed` 固定星座（支持中文/英文/符号，`--sign` 可覆盖）；
3. 按数据源优先级取**今日运势**；
4. 排版为 Text API 卡片（`title` / `message` / `signature`）；
5. 幂等检查（`data/state.json` 记录当天已推送则跳过，`--force` 强制重推）；
6. `POST …/device/{id}/text`（带重试），成功后写状态文件并打印卡片。

### 卡片效果（真实接口数据预览）

```text
==============================================
标题：章鱼AI.每日占一占
----------------------------------------------
♈ 白羊座 · 今日运势
「积累自身实力」★★★★☆ 73%
今日整体运势处于平稳向上的状态，日常事务推进顺畅，无突发变故干扰。……矛盾冲突…
爱情 56% · 事业 84% · 财运 14% · 健康 34%
宜 整理闲置物品 · 忌 沉迷刷短视频
幸运 草绿 · 17 · 贵人 狮子座
----------------------------------------------
签名：2026年10月26日 星期一 · 章鱼AI
==============================================
```

`--style full` 还会追加爱情/事业/财运/健康四段详细文本。

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
# 编辑 config.json：device_id、sign_mode/sign、style 等
export DOT_API_KEY='dot_app_xxxxxxxxx'   # API Key 只走环境变量
```

### 3. 验证

```bash
python3 run_daily.py doctor                 # 环境自检
python3 run_daily.py preview --sign 狮子座   # 预览排版（不推送）
python3 run_daily.py push --dry-run         # 走完整流水线但不调用 Dot 云
python3 run_daily.py push                   # ✅ 真实推送到设备
```

常用参数：

```bash
python3 run_daily.py push --sign libra      # 指定天秤座（本次）
python3 run_daily.py push --sign random     # 指定每日抽签
python3 run_daily.py push --style full      # 完整四段式
python3 run_daily.py push --force           # 忽略当天幂等，强制重推
python3 run_daily.py push --show-payload    # 打印请求体（凭据缺失时也会打印）
python3 run_daily.py push --dry-run --show-payload   # 只看排版+请求体，无需任何凭据
python3 run_daily.py push --provider xxapi  # 只用某个数据源
python3 run_daily.py signs                  # 列出全部星座写法
```

## 每日定时（任务 3 的“每日”）

### 方式 A：GitHub Actions（推荐，仓库已在 GitHub 上）

工作流：[.github/workflows/daily-push.yml](.github/workflows/daily-push.yml)
每天 **UTC 23:00（北京/澳门次日 07:00）** 自动运行“单测 → 推送”，并支持手动触发（可传入星座）。

一次性配置（仓库 **Settings → Secrets and variables → Actions**）：

| 名称 | 类型 | 值 |
| --- | --- | --- |
| `DOT_API_KEY` | **Secret**（只放 Secret） | Dot. App 导出的 API Key，形如 `dot_app_xxx` |
| `DOT_DEVICE_ID` | Secret **或** Variable | 设备序列号（工作流两处都会读，放错页面也能跑） |
| `ZHANGYU_SIGN` | Variable（可选） | 固定星座，如 `leo`；不设则按抽签 |

> 工作流第一步是 **Preflight（凭据自检）**：凭据缺失会立刻以
> `::error::` 注释 + Job Summary 给出配置位置，而不是等到推送时才报一句
> 「未配置设备序列号」。手动触发时勾选 **dry_run** 可以只预览卡片与请求体
> （不需要任何凭据）。

**排障：`错误：未配置设备序列号` / `Error: Process completed with exit code 2`**

1. `Settings → Secrets and variables → Actions` 里新建 `DOT_DEVICE_ID`
   （Secret 或 Variable 均可），值 = 设备序列号；`DOT_API_KEY` 必须是 Secret；
2. 只想先看输出：手动 Run workflow 时勾选 `dry_run`，或本地
   `python run_daily.py push --dry-run --show-payload`；
3. 本地 `python run_daily.py doctor --require-credentials` 与 CI 的预检等价，
   退出码 2 表示凭据不全（默认 `doctor` 不会因为缺凭据而失败）。

### 方式 B：本地 crontab

```cron
# 每天 07:00（机器时区为 UTC+8 时）推送
0 7 * * * cd /path/to/08-dot && DOT_API_KEY='dot_app_xxx' /usr/bin/python3 run_daily.py push >> logs/push.log 2>&1
```

> macOS/Linux 通用；确保 cron 环境能访问外网，并提前 `mkdir -p logs`。

## 配置项（config.json）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `device_id` | `""` | 设备序列号；环境变量 `DOT_DEVICE_ID` 优先 |
| `title` | `章鱼AI.每日占一占` | 卡片标题（任务 4） |
| `sign_mode` | `random` | `random`＝每日抽签 / `fixed`＝固定 `sign` |
| `sign` | `aries` | `fixed` 模式下的星座（中/英/符号均可） |
| `style` | `brief` | `brief` 精简 / `full` 含四段详细文本 |
| `max_chars` | `90` | 综合运势截断长度（尽量断在句读处） |
| `task_type` | `loop` | `loop` 循环列表 / `fixed` 固定列表 |
| `task_key` | `null` | fixed 列表项 key（配合 `--task-key`） |
| `task_alias` | 同标题 | 设备任务列表中的显示名 |
| `refresh_now` | `true` | 在线设备立即刷新循环内容 |
| `providers` | `["xxapi","freehoroscope","offline"]` | 数据源优先级 |
| `timezone` | `Asia/Macau` | 卡片日期/签名所用时区 |
| `icon_url` / `nfc_link` | `null` | 可选：40×40 图标 URL / NFC 跳转链接 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

35 项单测覆盖：星座解析、真实响应 fixture 解析与水印清理、排版/截断/星级、离线引擎确定性、Dot 客户端请求形态（本地 mock HTTP 服务，校验路径/鉴权/请求体）与错误码处理，以及 CLI 契约（凭据缺失时的行为、`--show-payload` / `--dry-run` 语义、占位 device_id、幂等跳过）。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `404` | 设备序列号不对，或设备列表里还没添加 **Text API** 内容（Dot. App → Content Studio）；`fixed` 模式需 `task_key` 与列表匹配 |
| `403` | 该 API Key 无权操作此设备（换绑/重建 Key） |
| `500` | 设备侧切换失败（设备离线/未响应），脚本会自动重试 3 次 |
| 推了但屏幕没变 | 循环内容需设备在线且 `refreshNow:true`；**固定列表**项只在 App 设定的刷新点更新 |
| 当天想重推 | `python3 run_daily.py push --force` |
| 接口全挂了 | 自动落到内置离线占卜引擎，卡片照常送达（数据源字段会标注“经降级”） |
| CI 报 `未配置设备序列号`（exit 2） | 仓库没配 `DOT_DEVICE_ID`（Secret 或 Variable 均可）；见上文「排障」 |
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
│   ├── signs.py                   # 十二星座元数据与解析（中/英/符号/别名）
│   ├── providers.py               # 数据源：xxapi → freehoroscope → 离线兜底
│   ├── formatter.py               # Text API 卡片排版（title/message/signature）
│   ├── dot_client.py              # Dot Text API 客户端（认证/重试/错误码）
│   └── cli.py                     # push / preview / signs / doctor
├── .github/workflows/daily-push.yml  # 每日定时推送（UTC 23:00 = 07:00 京/澳）
├── tests/                         # 35 项单测 + 真实接口 fixture
├── requirements.txt               # 零依赖说明
└── .gitignore                     # 排除 config.json / data/ / logs/
```

## 参考链接

- Dot Open Platform 仓库：<https://github.com/MindReset/dot_open_platform>
- 开发者平台文档：<https://dot.mindreset.tech/docs/service/open>
- Text API：<https://dot.mindreset.tech/docs/service/open/text_api>
- 主数据源：<https://xxapi.cn/doc/horoscope>
- 备数据源：<https://freehoroscopeapi.com/>

## License

MIT（跟随本仓库）。占星内容仅供娱乐参考。
