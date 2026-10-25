# 章鱼AI.每日占一占 — Quote/0 独立固件 (ESP-IDF)

不依赖 Dot 云：设备自己联网拉取**每日占星运程**，本地渲染上屏，每天 07:00 自动更新。

```text
上电/每日 07:00 唤醒
   → 联网 (NVS/串口配置的 WiFi)
   → SNTP 校时 (UTC+8)
   → 拉取运势: xxapi → freehoroscope → 内置离线占卜引擎
   → 16px 中文字库渲染 152x296 卡片 (标题: 章鱼AI.每日占一占)
   → 深度休眠至下一个 07:00 (网络故障 12 小时后重试)
```

## 构建 (GitHub Actions, 本仓库自带)

工作流 [`.github/workflows/build-firmware.yml`](../.github/workflows/build-firmware.yml)
在 Actions 页面 **Run workflow**（可选选择 standard / 首发版驱动），完成后从
**artifact `zhangyu-daily-bin`** 下载：

| 文件 | 说明 |
| --- | --- |
| `zhangyu_daily_full.bin` | **单文件整片镜像 (0x0 起)** ← 推荐 |
| `zhangyu_daily.bin` | 应用镜像 (0x10000) |
| `bootloader.bin` / `partition-table.bin` | 分区部件 |
| `烧录说明.txt` | 烧录与配网步骤 |

本地构建（需要 ESP-IDF v5.4+）：

```bash
. $IDF_PATH/export.sh
cd firmware
idf.py set-target esp32c3
idf.py build
```

## 烧录

```bash
pip install esptool
esptool.py --chip esp32c3 -p /dev/ttyACM0 erase_flash
esptool.py --chip esp32c3 -p /dev/ttyACM0 write_flash 0x0 zhangyu_daily_full.bin
```

> ⚠️ 覆盖自定义固件会**使保修失效**；出问题的恢复步骤见
> `dot_open_platform` 的 [custom-firmware-guide](https://github.com/MindReset/dot_open_platform/blob/main/devices/quote_0/docs/custom-firmware-guide.md)
> （无物理复位键：拆机断开锂电池 → 接 USB-C → 烧录 → 再接电池）。

## 首次配网 (USB 串口)

1. 屏幕显示 **「等待配置 WiFi」** 卡片；
2. 连接 USB，打开串口终端（控制台已是 USB Serial/JTAG）：

```bash
python -m esptool --chip esp32c3 -b 115200 monitor
# 或: screen /dev/ttyACM0 115200
```

3. 输入命令：

```text
zhangyu> wifi MyHomeSSID 12345678     # 保存 WiFi 并立即开始占卜
zhangyu> sign 狮子座                  # 可选: 固定星座 (支持中/英/符号)
zhangyu> sign random                  # 可选: 每日抽签 (默认)
zhangyu> show / forget / help / go
```

之后每天 **07:00** 自动：联网 → 取运势 → 渲染 → 休眠。
配置存 NVS，断电不丢；`forget` 清除 WiFi 回到配网模式。

### WiFi 由 GitHub 预设（推荐，免串口）

CI 构建时自动读取仓库的 Actions 配置并烧入固件。**约定：条目的
name = WiFi 名称（SSID），value = 密码**：

| 位置（Settings → Secrets and variables → Actions） | 读取方式 |
| --- | --- |
| **Variables**（推荐） | name = WiFi 名，value = 密码 —— CI 自动读取 |
| **Secrets**（固定名） | 变量名 `WIFI_PASSWORD`（或 `ZHANGYU_WIFI_PASSWORD`）= 密码，SSID 用变量 `WIFI_SSID` 提供 |

- 开放网络：value 留空；
- 同类条目多于一个时取**字母序第一个**（请只保留一条 WiFi 配置）；
- WiFi 名含空格（且不用固定命名）时，该名称作为 name 无法作为变量名，此时请
  改用 `WIFI_SSID=<WiFi名>` / `WIFI_PASSWORD=<密码>` 固定命名；
- ⚠️ 不要把 name=WiFi名 的条目放在 **Secrets** 页：GitHub 出于安全策略拦截
  批量读取 Secrets 的构建，请放 **Variables** 页（或用上面的固定命名）；
- 构建日志的 Annotations 会显示是否发现预设（`::notice::` 行，只回显 SSID）；
- 未设置任何条目 → 固件仍走下方的串口配网；
- 改动配置后重新 **Run workflow** 构建即可得到新 BIN。

本地构建注入也可用：`idf.py menuconfig` → 「章鱼AI.每日占一占」
填 `默认 WiFi SSID/密码`。

## 显示驱动版本

| 机型 (Dot. App 设备详情) | Kconfig |
| --- | --- |
| 标准版 | `ZHANGYU_EPD_UC8251D` (默认) |
| 首发版 / Early Bird | `ZHANGYU_EPD_UC8151` |

CI 用 workflow input `edition=early` 切换；本地用 `idf.py menuconfig`。

## 目录结构

```text
firmware/
├── CMakeLists.txt / sdkconfig.defaults / partitions_zhangyu.csv
├── main/
│   ├── app_main.c        # 主流程: 配网→联网→校时→取运势→上屏→休眠
│   ├── app_cfg.c         # NVS 配置 (WiFi/星座/唤醒计数)
│   ├── wifi_net.c        # STA 联网 (重连+超时)
│   ├── fortune.c         # xxapi → freehoroscope → 离线兜底 + 水印清理
│   ├── card.c            # 卡片布局 (标题/星级/指数/宜忌/幸运/日期)
│   ├── cli.c             # USB 串口配网命令行
│   └── Kconfig.projbuild
├── components/
│   ├── epd/              # UC8251D / UC8151 驱动 (Apache-2.0, 来自 dot_open_platform)
│   └── render/           # 帧缓冲 + UTF-8 排版 + font_gen.c (4040 字 16px)
└── tools/gen_font.py     # 字库生成 (文泉驿微米黑, Apache-2.0/GPLv3)
```

## 与云端方案的关系

`zhangyu/` Python 目录是另一条路线：**不刷机**，通过 Dot 开放平台 Text API
每日推送内容到设备（见根目录 README）。本目录是“纯固件”路线，二者可并存。

## 安全提示（摘自官方指南）

- 自定义固件**不享受官方安全保证**，且使保修失效；
- 深度休眠必须带唤醒源 —— 本固件每次休眠前**必定**设置定时唤醒；
- 控制台使用 Pure USB Serial/JTAG（UART 引脚与显示电源 GPIO20 冲突）。
