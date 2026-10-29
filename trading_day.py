#!/usr/bin/env python3
"""交易日闸：只在 A 股开市日跑数据流水线，非交易日直接跳过。

判据（按优先级，任一条命中即为"今天不跑"）：
1. 周末 —— 补班的周六 A 股也不开市，所以周末一律跳过。
2. 法定休市日表 HOLIDAYS —— 覆盖 2027 全年（国务院 2027 年放假安排：元旦 1/1-3、
   春节 2/5-11、清明 4/5、劳动节 5/1-5、端午 6/12-14、中秋 9/25-27、国庆 10/1-7）。
   ⚠ 每年 11 月国务院公布次年安排后要补表，否则只能靠判据 3 兜底（早 9:00 那次会误跑）。
3. 已过 09:35（今天该开盘了）但上证指数行情时间戳还停在旧日期 ⇒ 今天休市。
   这条是"硬证据"，能兜住节假日表漏补的年份。
4. 其余视为交易日。

输出：stdout 只打 `1`（跑）或 `0`（跳过），原因打到 stderr，便于 workflow 读。
"""

import datetime
import sys

HOLIDAYS = {
    "20261029",
    # 2027 年（国务院 2027 年部分节假日安排）
    "20270101", "20270102", "20270103",                            # 元旦
    "20270205", "20270206", "20270207", "20270208",
    "20270209", "20270210", "20270211",                            # 春节
    "20270405",                                                    # 清明
    "20270501", "20270502", "20270503", "20270504", "20270505",     # 劳动节
    "20270612", "20270613", "20270614",                            # 端午
    "20270925", "20270926", "20270927",                            # 中秋
    "20271001", "20271002", "20271003", "20271004",
    "20271005", "20271006", "20271007",                            # 国庆
}

QUOTE_URL = "https://qt.gtimg.cn/q=sh000001"     # 上证指数，第 30 个 ~ 分隔字段是 YYYYMMDDHHMMSS


def quote_day():
    """返回上证指数行情时间戳的日期(YYYYMMDD)，取不到返回 None。"""
    try:
        import requests
        r = requests.get(QUOTE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        txt = r.content.decode("gbk", "ignore")
        body = txt.split('"', 2)[1]
        f = body.split("~")
        ts = f[30]
        if len(ts) >= 8 and ts[:8].isdigit():
            return ts[:8]
    except Exception as e:
        print("行情取不到(%s)，按交易日处理" % e, file=sys.stderr)
    return None


def decide(now=None):
    now = now or datetime.datetime.now()
    d = now.strftime("%Y%m%d")
    if now.weekday() >= 5:
        return False, "周末休市"
    if d in HOLIDAYS:
        return False, "法定休市日"
    if now.hour * 60 + now.minute >= 9 * 60 + 35:
        qd = quote_day()
        if qd and qd != d:
            return False, "行情仍停在 %s，今天未开盘" % qd
    return True, "交易日"


if __name__ == "__main__":
    ok, why = decide()
    print("1" if ok else "0")
    print("%s %s ⇒ %s（%s）" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "开市" if ok else "休市", "跑" if ok else "跳过", why),
          file=sys.stderr)
