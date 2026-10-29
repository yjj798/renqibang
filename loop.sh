#!/bin/bash
# 每天只跑3次: 9:00 / 10:00 / 13:00 (节流)
while true; do
  h=$(date +%H); m=$(date +%M); dow=$(date +%u)
  mins=$((10#$h*60+10#$m))
  run=0
  if [ "$dow" -le 5 ]; then
    if [ "$mins" -ge 540 ] && [ "$mins" -lt 548 ]; then run=1; fi
    if [ "$mins" -ge 600 ] && [ "$mins" -lt 608 ]; then run=1; fi
    if [ "$mins" -ge 780 ] && [ "$mins" -lt 788 ]; then run=1; fi
  fi
  if [ "$run" = "1" ]; then
    cd "$(dirname "$0")" && python3.11 update_data.py >> loop.log 2>&1
  fi
  sleep 300
done
