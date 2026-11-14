#!/usr/bin/env bash
# 把個股體檢的程式複製到記憶中樞的全域技能 tw-stock-check（任何專案、雲端 session 都能用）。
# 這個專案是原始碼；改了 stock_check.py 或 twstock_lab/ 之後跑一次，再執行 hub_cli.py sync。
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
dest="${1:-$HOME/.claude/ai-memory-hub/skills/tw-stock-check/scripts}"
mkdir -p "$dest/twstock_lab"
cp "$here/stock_check.py" "$dest/stock_check.py"
cp "$here/theme_screen.py" "$dest/theme_screen.py"
cp "$here"/twstock_lab/{__init__,fetch,prep,strategies,backtest}.py "$dest/twstock_lab/"
cp "$here/twstock_lab/daytrade_branches.json" "$dest/twstock_lab/"
echo "已複製到 $dest"
