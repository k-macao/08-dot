#!/usr/bin/env python3
"""章鱼AI.行情速报 — 入口脚本。

    python3 run_daily.py push        # 取最新指数行情并推送设备（交易时段任务加 --trading-only）
    python3 run_daily.py preview     # 只预览卡片
    python3 run_daily.py indices     # 查看卡片上的指数清单
    python3 run_daily.py doctor      # 环境自检
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zhangyu.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
