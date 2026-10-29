#!/bin/bash
# ⚠ 已被 GitHub Actions 接管（.github/workflows/update.yml：每天 4 次，跑完自动提交）。
#   本脚本只在 Actions 不可用时做本地兜底。
#   启用前必须先停掉 Actions 的 schedule，否则两边交替覆盖、commit 互相打架。
#   兜底时跑完要自己提交：git add data.json index.html history.json && git commit && git push
#
# 时点与 Actions 对齐：10:05 / 12:05 / 15:05 / 16:35
while true; do
  h=$(date +%H); m=$(date +%M); dow=$(date +%u)
  mins=$((10#$h*60+10#$m))
  run=0
  if [ "$dow" -le 5 ]; then
    for t in 605 725 905 995; do
      if [ "$mins" -ge "$t" ] && [ "$mins" -lt $((t + 8)) ]; then run=1; fi
    done
  fi
  if [ "$run" = "1" ]; then
    cd "$(dirname "$0")" && python update_data.py >> loop.log 2>&1
  fi
  sleep 300
done
