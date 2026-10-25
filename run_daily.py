#!/usr/bin/env python3
"""章鱼AI.每日占一占 — 入口脚本。

    python3 run_daily.py push        # 获取今日运势并推送设备
    python3 run_daily.py preview     # 只预览卡片
    python3 run_daily.py doctor      # 环境自检
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zhangyu.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
