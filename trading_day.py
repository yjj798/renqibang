#!/usr/bin/env python3
"""交易日闸：只在 A 股开市日跑数据流水线，非交易日直接跳过。

判据（按优先级，任一条命中即为"今天不跑"）：
1. 周末 —— 补班的周六 A 股也不开市，所以周末一律跳过。
2. 法定休市日表 HOLIDAYS —— 覆盖 2027 全年（国务院 2027 年放假安排：元旦 1/1-3、
   春节 2/5-11、清明 4/5、劳动节 5/1-5、端午 6/12-14、中秋 9/25-27、国庆 10/1-7）。
   ⚠ 每年 11 月国务院公布次年安排后要补表，否则只能靠判据 3 兜底（早 9:00 那次会误跑）。
3. 已过 09:35（今天该开盘了）但**两个独立数据源都说"今天没有行情"** ⇒ 今天休市。
   这条是"硬证据"，能兜住节假日表漏补的年份。
4. 其余视为交易日。

判据 3 为什么用两个源、且"任一说今天 ⇒ 就开市"：
- 腾讯 `qt.gtimg.cn` 的行情时间戳（第 30 个 ~ 字段）和东财 `push2his` 日 K 最后一根，
  是两个**完全独立的系统**。单看一个，源侧抽风（返回滞留缓存、字段位移）就会把正常交易日误判成休市，
  代价是当天整天不更新数据——比多跑一次严重得多。
- 所以取保守方向：**只要有一个源报"今天有数据"就判开市**，只有取到的源全部说"不是今天"才判休市。
  两个源同时错的可能性极低，而单源故障不会造成误跳。

输出：stdout 只打 `1`（跑）或 `0`（跳过），原因打到 stderr，便于 workflow 读。
"""

import datetime
import sys

HOLIDAYS = {
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
KLINE_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
             "?secid=1.000001&klt=101&fqt=1&lmt=1&fields1=f1&fields2=f51&end=20500101")


def _gtimg_day():
    """腾讯：上证指数行情时间戳的日期(YYYYMMDD)，取不到返回 None。"""
    import requests
    r = requests.get(QUOTE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    body = r.content.decode("gbk", "ignore").split('"', 2)[1]
    ts = body.split("~")[30]
    return ts[:8] if len(ts) >= 8 and ts[:8].isdigit() else None


def _emkline_day():
    """东财：上证指数日K最后一根 K 线的日期(YYYYMMDD)，取不到返回 None。

    日K与分钟级快照是两套系统，休市日绝不会凭空多一根；境外 runner 也能拿到
    （实测 push2his 200，而 push2.eastmoney.com 不通 —— 别拿后者否掉整条链路）。
    """
    import requests
    r = requests.get(KLINE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    kl = ((r.json().get("data") or {}).get("klines") or [])
    if not kl:
        return None
    d = str(kl[-1]).split(",")[0].split(" ")[0]    # "2026-10-29" / "2026-10-29 15:00"
    d = d.replace("-", "")
    return d if len(d) == 8 and d.isdigit() else None


def quote_day(verbose=True):
    """两个独立源各取一次今日行情日期，返回 {源名: YYYYMMDD 或 None}。

    某个源取不到只降级、不抛错：判据本身就是"取到的源全部说不是今天才判休市"，
    少一个源等于退化成单源，仍然安全。所以这里的失败是预期内的常态（限流、被拦、字段变动）。
    """
    out = {}
    for name, fn in (("腾讯行情", _gtimg_day), ("东财日K", _emkline_day)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = None
            if verbose:
                print("%s 取不到(%s)，降级为单源判断" % (name, type(e).__name__), file=sys.stderr)
    return out


def decide(now=None):
    now = now or datetime.datetime.now()
    d = now.strftime("%Y%m%d")
    if now.weekday() >= 5:
        return False, "周末休市"
    if d in HOLIDAYS:
        return False, "法定休市日"
    if now.hour * 60 + now.minute >= 9 * 60 + 35:
        # 09:35 之前不能用：盘前两个源都还是上一交易日，会把每个交易日的 9:00 那次误杀。
        days = {k: v for k, v in quote_day().items() if v}
        if days:
            print("  源读数: " + " | ".join("%s=%s" % (k, v) for k, v in days.items()),
                  file=sys.stderr)
            if all(v != d for v in days.values()):
                return False, "行情/日K 全部停在 %s，今天未开盘（休市）" % "/".join(sorted(set(days.values())))
    return True, "交易日"


if __name__ == "__main__":
    ok, why = decide()
    print("1" if ok else "0")
    print("%s %s ⇒ %s（%s）" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "开市" if ok else "休市", "跑" if ok else "跳过", why),
          file=sys.stderr)
