#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人气榜/关注度四榜自动更新脚本
原始榜: 市场关注度前100全量(无筛选) 4列
精选榜: 关注度前100 ∩ 实时价<10 ∩ MA5向上 4列
增长 = 当前值 ÷ 自身前4个交易日均值 - 1 (盘中热度按已交易时段折算)
仅 15:05 后写历史
"""
import json, time, os, sys, datetime, math
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import free_data as F

_KLINE_CACHE = {}


HIST_FILE = os.path.join(HERE, "history.json")
# 输出文件: 默认与本脚本同目录(本地部署可直接用), 可用环境变量 OUTPUT_DIR 覆盖
OUT_FILE = os.path.join(os.environ.get("OUTPUT_DIR", HERE), "data.json")
# 页面里的 EMBEDDED 兜底常量要跟着每次结果刷新, 否则首屏永远是上一次手工写入的旧快照
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(OUT_FILE)), "index.html")

# ---- 价格因子（2026-10-29 20:24 用户令：低价股容易成妖，10 元以下优先给分）----
PF_MAX_BONUS = 0.18      # 2 元处总加成上限 +18%
PF_BASE_BONUS = 0.03     # 10 元处起点 +3%（10~20 元只做可介入性微调）
PF_SWEET_PRICE = 10.0    # 低价优先区上限
PF_CAP_PRICE = 20.0      # ≥ 20 元不加也不罚（贵不等于差）
PF_FLOOR_PRICE = 2.0     # < 2 元封顶 +10%（面值退市风险，不给满额）
# ---- 位置因子：股价在近 60 日高低区间的相对位置，越低（越靠底部）越加分 ----
POS_BONUS = 0.08         # 处于区间下三分之一时最高 +8%
POS_WINDOW = 60          # 回看交易日数
# 榜单准入的价格上限：这是"买不买得起"的执行约束，不是评分项，别和上面几个混为一谈
MAX_PRICE = 20.0
# 评分封顶：100 分制。各因子是乘性叠加的，不封顶会算出 117 分这种数字
SCORE_CAP = 100.0

def prev_trade_dates(n):
    d = datetime.date.today()
    out = []
    while len(out) < n:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out

def elapsed_frac():
    """已交易时段占全天比例(A股 9:30-11:30, 13:00-15:00 共240分钟)"""
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return 1.0
    m = now.hour * 60 + now.minute
    if m < 570:
        return 1.0
    if m < 690:
        e = m - 570
    elif m < 780:
        e = 120
    else:
        e = 120 + (m - 780)
    return max(0.2, min(1.0, e / 240.0))

# ---------- 历史种子 ----------
HEAT_SEED = {
"20261015": {"300015.SZ":199486,"002354.SZ":996631,"002249.SZ":86870,"601398.SH":1107175,"601288.SH":718493,"600795.SH":100214,"600558.SH":47098,"001979.SZ":92234,"600715.SH":167047,"601956.SH":49958,"002307.SZ":26177,"600828.SH":24557,"601985.SH":262610,"600892.SH":203191,"000888.SZ":26131},
"20261016": {"300015.SZ":179405,"002354.SZ":761940,"002249.SZ":82742,"601398.SH":691051,"601288.SH":463788,"600795.SH":87245,"600558.SH":52380,"001979.SZ":186173,"600715.SH":104618,"601956.SH":41398,"002307.SZ":27384,"600828.SH":23158,"601985.SH":205606,"600892.SH":134950,"000888.SZ":20904},
"20261019": {"300015.SZ":229694,"002354.SZ":869674,"002249.SZ":133198,"601398.SH":1263360,"601288.SH":1118566,"600795.SH":95650,"600558.SH":91774,"001979.SZ":112759,"600715.SH":76195,"601956.SH":56134,"002307.SZ":22997,"600828.SH":20976,"601985.SH":254479,"600892.SH":81149,"000888.SZ":29071},
"20261020": {"300015.SZ":175229,"002354.SZ":1177937,"002249.SZ":128107,"601398.SH":858190,"601288.SH":697932,"600795.SH":87876,"600558.SH":70802,"001979.SZ":95114,"600715.SH":69401,"601956.SH":89198,"002307.SZ":35822,"600828.SH":20760,"601985.SH":186149,"600892.SH":62148,"000888.SZ":21062},
"20261021": {"300015.SZ":203215,"002354.SZ":1284302,"002249.SZ":158299,"601398.SH":705670,"601288.SH":498245,"600795.SH":103447,"600558.SH":124111,"001979.SZ":91243,"600715.SH":406687,"601956.SH":65047,"002307.SZ":42677,"600828.SH":20578,"601985.SH":151454,"600892.SH":124961,"000888.SZ":18238},
"20261022": {"300015.SZ":246778,"002354.SZ":830549,"002249.SZ":956906,"601398.SH":1523448,"601288.SH":782143,"600795.SH":99650,"600558.SH":182556,"001979.SZ":78485,"600715.SH":251736,"601956.SH":46135,"002307.SZ":508459,"600828.SH":18317,"601985.SH":164227,"600892.SH":170093,"000888.SZ":20179},
"20261023": {"300015.SZ":281724,"002354.SZ":1863962,"002249.SZ":1851223,"601398.SH":1324951,"601288.SH":734801,"600795.SH":96463,"600558.SH":95844,"001979.SZ":103884,"600715.SH":121690,"601956.SH":39034,"002307.SZ":785323,"600828.SH":19339,"601985.SH":182357,"600892.SH":97092,"000888.SZ":25243},
"20261026": {"300015.SZ":403349,"002354.SZ":4355201,"002249.SZ":1143214,"601398.SH":1265302,"601288.SH":542890,"600795.SH":147343,"600558.SH":402216,"001979.SZ":484255,"600715.SH":86734,"601956.SH":38693,"002307.SZ":370980,"600828.SH":29016,"601985.SH":191940,"600892.SH":132187,"000888.SZ":25997},
"20261027": {"300015.SZ":273910,"002354.SZ":3219773,"002249.SZ":1996966,"601398.SH":1616508,"601288.SH":700858,"600795.SH":173880,"600558.SH":1058270,"001979.SZ":197736,"600715.SH":453463,"601956.SH":263237,"002307.SZ":303706,"600828.SH":375761,"601985.SH":187346,"600892.SH":197810,"000888.SZ":347004},
"20261028": {"300015.SZ":225211,"002354.SZ":1774951,"002249.SZ":3252084,"601398.SH":982862,"601288.SH":494390,"600795.SH":119170,"600558.SH":662206,"001979.SZ":120886,"600715.SH":636850,"601956.SH":704304,"002307.SZ":231319,"600828.SH":887882,"601985.SH":217894,"600892.SH":186343,"000888.SZ":243734},
}
ATTN_SEED = {
"20261022": {"601398.SH":1578,"002249.SZ":599,"601288.SH":929,"300015.SZ":4055,"002307.SZ":393},
"20261023": {"002249.SZ":988,"300015.SZ":4158,"600795.SH":980,"002307.SZ":547,"000888.SZ":980},
"20261026": {"601398.SH":1736,"601288.SH":901,"300015.SZ":4164,"600795.SH":604,"601956.SH":373,"001979.SZ":617,"000888.SZ":2307},
"20261027": {"601398.SH":1756,"002249.SZ":1658,"601288.SH":856,"600795.SH":703,"002307.SZ":586,"601956.SH":708,"600693.SH":358,"002354.SZ":2354,"600261.SH":344,"000888.SZ":324},
"20261028": {"002249.SZ":2068,"601288.SH":841,"600795.SH":701,"002307.SZ":578,"601956.SH":517,"001979.SZ":632},
}

def load_hist():
    """只保留东财人气榜口径(20261029起)的排名历史, 旧热度值口径已废弃。

    rate = 同花顺 day 榜热度值(绝对值)快照。2026-10-29 起开始累积 —— 攒够样本后
    「热度值日环比」就是纯两源(同花顺+55188)口径, 不必再逐股打东财 getHisList。"""
    if os.path.exists(HIST_FILE):
        raw = json.load(open(HIST_FILE, encoding="utf-8"))
        return {"heat": {d: v for d, v in raw.get("heat", {}).items() if d >= "20261029"},
                "attn": {d: v for d, v in raw.get("attn", {}).items() if d >= "20261029"},
                "rate": raw.get("rate", {}),
                "cpt": raw.get("cpt", {})}
    return {"heat": {}, "attn": {}, "rate": {}}

def avg_prev(hist, kind, code, today, n=4):
    ds = sorted([d for d in hist[kind] if d < today and code in hist[kind][d]])[-n:]
    vals = [hist[kind][d][code] for d in ds if hist[kind][d][code]]
    return sum(vals) / len(vals) if vals else None

def fetch_quotes(codes, pool):
    """腾讯实时行情: 最新价/涨跌幅/换手率/流通市值"""
    qs = F.fetch_quotes_tencent(codes)
    for c, v in qs.items():
        if c in pool:
            pool[c].update(v)


def fetch_heat(codes, pool):
    """东财人气榜口径: 排名即热度, 无绝对热度值"""
    return {}


POP_AVG_N = 5     # 热度增长的基数: 前 N 个交易日的平均人气排名


def fetch_pop_hist_all(codes, pool, today, workers=8):
    """给每只股票补上「今日人气排名 + 前5个交易日均值」—— 热度增长的基数就在这里。

    口径: 增长 = (前5日均排名 − 今日排名) / 前5日均排名 × 100，正=关注度上升。
    不用同花顺 hot_rank_chg 的原因见 free_data.fetch_pop_hist 的注释（盘后整批为 0）。

    ⚡ 并发 8 路（原来串行 151 只 + 每只 sleep 0.08 ⇒ ~20s，现在 ~4s）。"""
    def _one(c):
        try:
            return c, F.fetch_pop_hist(c)
        except Exception:
            return c, []

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, rows in ex.map(_one, codes):
            if not rows:
                continue
            today_rk, past = None, rows
            if rows[-1]["date"] == today:
                today_rk, past = rows[-1]["rank"], rows[:-1]
            if today_rk:
                pool[c]["heat_rank"] = today_rk          # 东财全市场口径，L 里按 5580 算
                pool[c]["pop_rank"] = today_rk
                ok += 1
            prev = [r["rank"] for r in past[-POP_AVG_N:]]
            if prev:
                pool[c]["pop_avg"] = round(sum(prev) / len(prev), 1)
                pool[c]["pop_prev"] = prev[-1]
    print("  人气排名历史: %d/%d 条" % (ok, len(codes)))


def _get_kline(code, n=75):
    """取 K 线数据(带缓存), 所有需要 K 线的函数共用这一份"""
    if code in _KLINE_CACHE:
        return _KLINE_CACHE[code]
    try:
        r = requests.get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
            params={"symbol": F._sym(code), "scale": 240, "len": n},
            headers=F.UA, timeout=12)
        rows = r.json() or []
        _KLINE_CACHE[code] = rows
        return rows
    except Exception:
        _KLINE_CACHE[code] = []
        return []


def fetch_klines_concurrent(codes, n=75, workers=8):
    """并发预取所有 K 线数据, 一次到位"""
    def _one(c):
        _get_kline(c, n)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, codes))


def fetch_position(codes, pool):
    """位置分: 从缓存 K 线算, 不再单独请求"""
    for c in codes:
        rows = _get_kline(c, POS_WINDOW + 15)[-POS_WINDOW:]
        if len(rows) >= 20:
            hi = max(float(x["high"]) for x in rows)
            lo = min(float(x["low"]) for x in rows)
            p = pool[c].get("price")
            if p and hi > lo:
                pool[c]["pos60"] = round(max(0.0, min(1.0, (p - lo) / (hi - lo))), 3)


def ma5up_filter(codes, pool):
    """从缓存 K 线算 MA5 方向, 不再单独请求"""
    keep = []
    for c in codes:
        closes = [float(x["close"]) for x in _get_kline(c, 8)]
        if len(closes) >= 6:
            if sum(closes[-5:]) / 5 > sum(closes[-6:-1]) / 5:
                keep.append(c)
    return keep


def fetch_turnover_avg(codes, pool, d1, d2):
    """从缓存 K 线算历史换手均值, 不再单独请求"""
    for c in codes:
        vols = _get_kline(c, 21)
        if len(vols) < 7:
            continue
        t = pool[c].get("turnover")
        if not t:
            continue
        today_iso = vols[-1]["day"]
        vt = float(vols[-1]["volume"])
        if not vt:
            continue
        hist_v = [float(x["volume"]) for x in vols[-6:-1]]
        pool[c]["turnover_avg"] = round(sum(t * v / vt for v in hist_v) / len(hist_v), 2)


def fetch_ma_stick(codes, pool):
    """均线粘合度: 从缓存 K 线算 MA5/10/20, 不再调 iFinD API"""
    for c in codes:
        closes = [float(x["close"]) for x in _get_kline(c, 70)]
        if len(closes) < 20:
            continue
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        vals = [ma5, ma10, ma20]
        mid = sum(vals) / 3
        if mid > 0:
            pool[c]["ma_stick"] = round((max(vals) - min(vals)) / mid * 100, 1)


def assemble(codes, pool, hist, today, frac):
    stocks = []
    for c in codes:
        p = pool[c]
        # 热度增长: 优先进东财逐日排名口径(前5日均排名 → 今日排名),
        # 其次本项目自建历史, 最后才用接口自带的当日变化 hisRc 兜底。
        h = p.get("heat_rank")
        h_avg = p.get("pop_avg") or avg_prev(hist, "heat", c, today)
        g_cross = round((h_avg - h) / h_avg * 100, 1) if h and h_avg else None
        if g_cross is None and h:
            # 兜底: 用东财自带的今日排名变化(hisRc)反推昨日排名。
            # hisRc 正 = 排名上升, 昨日排名数字更大 ⇒ 昨日 = 今日 + hisRc（**不是减**）:
            # 今日 41 名、hisRc +520 ⇒ 昨日 561 名, 增长 (561-41)/561 = 92.7%。
            chg_r = p.get("pop_chg") or 0
            prev_r = h + chg_r
            if prev_r > 0:
                g_cross = round((prev_r - h) / prev_r * 100, 1)
        # 日内加速: 同花顺 day 榜 vs hour 榜的名次差。day#94 → hour#36 = 最近一小时在升温(+61.7%)。
        # 这是唯一能在收盘后还保留"当日动能"的字段(hot_rank_chg 盘后整批为 0)。
        d_o, h_o = p.get("ths_day_order"), p.get("ths_hour_order")
        g_accel = round((d_o - h_o) / d_o * 100, 1) if d_o and h_o else None
        # 合成: 跨日增长为主 + 日内加速只做**正向加成**(不取平均)。
        # ⚠ 不能按 0.65/0.35 加权平均: day 榜第 1 名的加速上限就是 0(它已经在第一),
        #   day 榜第 94 名却能算出 +61% —— 平均会把跨日高增长的头部票系统性地压下去
        #   (实测文投控股 跨日97.5 + 加速9.4 被平均成 66.7, 直接掉出前三)。
        if g_cross is None and g_accel is None:
            heat_g = None
        elif g_cross is None:
            heat_g = g_accel
        else:
            heat_g = round(g_cross + 0.4 * max(0.0, g_accel or 0.0), 1)
        attn_g = None
        # 换手启动: 今日换手(盘中折算) / 5日平均换手 - 1
        t = p.get("turnover"); ta = p.get("turnover_avg")
        t_eff = (t / frac) if t and frac < 1.0 else t
        t_ratio = round((t_eff / ta - 1) * 100, 1) if t_eff and ta else None
        chg = p.get("chg") or 0
        limit_up = chg >= 9.8 or chg >= 19.5
        # 换手启动因子: 放量最多+15%, 缩量-5%; 涨停一字板换手失真, 用热度排名补位(因子取1.0)
        t_factor = 1.0 if limit_up else (0.95 + 0.05 * max(0, min(2, (t_ratio or 0) / 100.0)))
        # 换手方向: 上涨放量=向上换手(健康); 下跌放量=向下换手(出货, 惩罚); 下跌缩量=止跌观察
        p["swap_down"] = (not limit_up) and chg < 0 and (t_ratio or 0) > 20
        if p["swap_down"]:
            t_factor *= 0.9
        # 启动形态因子(以襄阳轴承10-22启动日为标杆):
        #   低位换手启动(5日均换手<=5%且今日放量>30%) +7% —— 起涨前换手率低
        #   首启放量(今日换手较5日均+50%以上且股价上涨) +8% —— 第一次启动换手率
        #   均线粘合(MA5/10/20极差<=3%) +5% —— 变盘前夕
        launch = []
        lf = 1.0
        if ta and ta <= 5 and (t_ratio or 0) >= 30:
            lf *= 1.07; launch.append("低位启动")
        if (t_ratio or 0) >= 50 and chg > 0:
            lf *= 1.08; launch.append("首启放量")
        ms = p.get("ma_stick")
        if ms is not None and ms <= 3:
            lf *= 1.05; launch.append("均线粘合")
        p["launch"] = "+".join(launch)
        t_factor *= lf
        p["turnover_ratio"] = t_ratio
        p["t_factor"] = round(t_factor, 3)
        p["limit_up"] = limit_up
        price = p.get("price")
        # 价格因子（用户令 2026-10-29 20:24：低价股容易成妖，10 元以下优先）
        #   ≥ 20 元   1.00        贵不等于差，不罚
        #   10~20 元  1.00 → 1.03 只做可介入性微调
        #   2~10 元   1.03 → 1.18 低价区，越便宜加越多
        #   < 2 元    1.10        有面值退市风险，不给满额
        if not price:
            pf = 1.0
        elif price < PF_FLOOR_PRICE:
            pf = 1.10
        elif price < PF_SWEET_PRICE:
            pf = round(1 + PF_BASE_BONUS
                       + (PF_SWEET_PRICE - price) / (PF_SWEET_PRICE - PF_FLOOR_PRICE)
                       * (PF_MAX_BONUS - PF_BASE_BONUS), 3)
        elif price < PF_CAP_PRICE:
            pf = round(1 + (PF_CAP_PRICE - price) / (PF_CAP_PRICE - PF_SWEET_PRICE) * PF_BASE_BONUS, 3)
        else:
            pf = 1.0
        p["pf"] = pf
        # 位置因子（用户令：位置好的加分）：股价在近 60 日高低区间的位置，0=底部 1=顶部
        pos = p.get("pos60")
        if pos is None:
            posf = 1.0                     # 拿不到 K 线就中性，不因缺数据惩罚
        elif pos <= 0.35:
            posf = 1 + POS_BONUS           # 区间下三分之一 = 低位
        elif pos <= 0.65:
            posf = 1 + POS_BONUS / 2
        else:
            posf = 1.0                     # 已在高位，不加（也不扣）
        p["pos_factor"] = round(posf, 3)
        stocks.append({
            "code": c, "name": p["name"], "price": p.get("price"), "chg": p.get("chg"),
            "float_mv": p.get("float_mv"),
            "attn": p["attn"], "attn_rank": p["attn_rank"], "attn_growth": attn_g,
            "heat": h, "heat_rank": p.get("heat_rank"), "heat_growth": heat_g,
            "g_cross": g_cross, "g_accel": g_accel,
            "boards": p.get("boards") or 0, "board_tag": p.get("board_tag") or "",
            "lhb_org_net": (p.get("lhb") or {}).get("org_net") or 0,
            "lhb_net": (p.get("lhb") or {}).get("net") or 0,
            "turnover": p.get("turnover"), "turnover_ratio": p.get("turnover_ratio"),
            "swap_down": p.get("swap_down", False),
            "launch": p.get("launch", ""),
            "limit_up": p.get("limit_up", False),
            "pf": p.get("pf"),
            "pos60": p.get("pos60"), "pos_factor": p.get("pos_factor", 1.0),
            "score_final": None,
            "concept": "", "is_leader": False,
        })
    return stocks

# (fetch_turnover_avg / fetch_ma_stick 已上移到 K 线缓存版本)


FRY = {}      # 55188「明日主题」: {题材名: {title, limit_up, is_new, ...}} —— 概念催化


def _fry_for(cpt):
    """概念名 → 55188 题材催化条目(先精确、再包含匹配)"""
    if not cpt or not FRY:
        return None
    if cpt in FRY:
        return FRY[cpt]
    for k, v in FRY.items():
        if len(k) >= 3 and (k in cpt or cpt in k):
            return v
    return None


def fetch_concepts(codes, pool, workers=8):
    """题材归属 = 异动解读 keywordList ⊕ 原三源(东财F10 ∪ 同花顺concept_tag ∪ 55188龙虎榜topics)
    —— 用户令(2026-10-30 00:09): 「题材不准, 题材叠加原来那套, 一个股多个题材, 避免判断失误」。

    异动解读是"今天在炒什么"的活题材(羊字辈/机器人轴承), 但只覆盖当日有异动的票且词形自由;
    原三源是"票挂了什么板块"的死标签, 胜在稳定全覆盖。两套合并: 集群/风口评测在合并集上跑,
    一只票可以同时挂多个题材; 异动解读的过滤(财报词/跌因/风险提示)只管它自己的关键词,
    不动原三源(那些本来就是规范概念名)。"""
    import re as _re
    _fund = _re.compile(r"增长|扭亏|营收|净利|盈利|亏损|预增|预减|增速|业绩")
    # 下跌解读的关键词是"为什么跌"(如「元件板块下跌」), 不是题材风口;
    # 风险提示类短语(此前涨幅较大)同理 —— 都不配进题材池。
    _fall = _re.compile(r"下跌|大跌|跌停|回调|下挫|走弱|调整")
    _risk = _re.compile(r"此前|涨幅较大|风险|警示|波动|异动|减持|质押")
    # 概念利多强度信号(用户令 2026-10-30 00:21: 概念是否符合它/利多大不大, 看逻辑正文):
    # 硬动作(中标/签订/试制完成/取得注册证…) = 真催化; 纯远景(尚处拓展阶段/占比极小/未产生) = 蹭概念
    _strong = _re.compile(r"中标|签订|合同|订单|获批|取得|注册证|收购|试制完成|送样|揭幕|上线|开业|投产|交付|突破|首家|率先|荣获|独家|研发成功|批复|准产|揭牌|开通")
    _weak = _re.compile(r"尚处|尚未|未产生|拓展阶段|研究阶段|筹备|拟建|规划中|探索|较小|影响有限|占比")

    def _benefit(rsn, cs):
        if not rsn:
            return ""
        s_n = len(_strong.findall(rsn))
        if s_n >= 2:
            return "强"
        if s_n == 1:
            return "中"
        # 无硬动作, 但概念词在正文里有实质呼应(如羊字辈←生肖行情报道) → 至少中
        if any(k in rsn for k in cs if len(k) >= 2):
            return "中"
        if _weak.search(rsn):
            return "弱"
        return "中"

    def _one(c):
        pc = pool[c]
        cs, kwall, tag, rsn, blv = [], "", "", "", ""
        # 1) 活题材: 异动解读(当日)
        try:
            a = F.fetch_ths_anomaly(c)
        except Exception:
            a = None
        if a:
            tag = a.get("tag") or ""
            rsn = a.get("reason") or ""
            kwall = "+".join(k.strip() for k in (a.get("keywords") or []) if k.strip())
            if not _fall.search(tag):
                for k in a.get("keywords") or []:
                    nb = F.norm_concept(k)
                    # 同一题材的引号形态(“羊”字辈 / 羊字辈)要并成一家, 否则集群被劈开
                    if nb:
                        nb = nb.replace("“", "").replace("”", "").replace('"', "").strip()
                    if (nb and nb not in cs and not F.is_generic_concept(nb)
                            and not _fund.search(nb) and not _fall.search(nb)
                            and not _risk.search(nb)):
                        cs.append(nb)
        # 2) 死标签兜底: 同花顺 concept_tag ∪ 55188 龙虎榜 topics ∪ 东财 F10
        extras = list(pc.get("ths_tags") or []) + list((pc.get("lhb") or {}).get("topics") or [])
        try:
            extras += list(F.fetch_concepts_em(c))
        except Exception:
            pass
        for extra in extras:
            nb = F.norm_concept(extra)
            if nb and nb not in cs and not F.is_generic_concept(nb) and nb not in EXCLUDE_GLOBAL:
                cs.append(nb)
        blv = _benefit(rsn, cs)
        return c, cs, kwall, tag, rsn, blv

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, cs, kwall, tag, rsn, blv in ex.map(_one, codes):
            pc = pool[c]
            pc["concepts"] = cs
            pc["kw_all"] = kwall
            pc["benefit_lv"] = blv
            pc["anomaly"] = {"tag": tag, "reason": rsn} if tag or rsn else None

# 交易/指数属性 + 泛概念 + 年报/预增类伪概念(东财口径补充)
EXCLUDE_GLOBAL = {"融资融券","转融券标的","沪股通","深股通","同花顺漂亮100","MSCI中国",
               "富时罗素","标普道琼斯A股","证金持股","机构重仓","基金重仓","社保重仓",
               "QFII重仓","券商重仓","险资重仓","预盈预增","预亏预减","举牌","股权转让","回购增持",
               "百日新高","百日新低","连续上涨","连续下跌","创历史新高","创历史新低",
               "创月新高","创半年新高","创年新高","近期新高","近期新低",
               "国企改革","央企改革","地方国企改革","华为概念","华为汽车","汽车整车",
               "高股息精选","同花顺中特估100","中特估","央企控股","破净股","低价股",
               "已知龙头股","注册制次新股","深股通中标","沪股通中标","AH股","GDR",
               "人民币贬值受益","一带一路","军民融合","西部大开发","乡村振兴",
               "2026中报预增","2025年报预增","年报预增","中报预增","三季报预增",
               "同花顺出海50","宁组合","茅指数","百元股","绩优股","微盘股","ST板块",
               "次新股","含可转债","转债标的","壳资源","独角兽","兜底式增持",
               "题材股","小盘股","大盘股","中盘股","最近多板","东方财富热股","昨日高振幅",
               "昨日高换手","昨日涨停","昨日连板","昨日触板","标准普尔","深成500","股权激励",
               "电池技术","数据中心","液冷服务器","央国企改革","基金增仓","基金减仓","HS300_",
               "昨日涨停_含一字","昨日连板_含一字","深证100","中证1000","中证800","百亿级私募持仓","QFII新进","券商金股",
               "2026中报首亏","2026中报预增","首亏","扭亏","电子","机械设备","军工","银行","煤炭",
               "钢铁","有色金属","化工","医药生物","食品饮料","计算机","通信","汽车","电力设备",
               "家用电器","农林牧渔","房地产","建筑装饰","交通运输","纺织服装","公用事业",
               "商业贸易","建筑材料","国防军工","综合","轻工制造","美容护理","环保","石油石化"}

def add_concept_stats(stocks, pool):
    """概念集群统计 + 影子概念合并 + 龙头/杂毛判定 + 概念系数"""
    groups = {}
    for x in stocks:
        for cpt in pool[x["code"]].get("concepts", []):
            cpt = F.norm_concept(cpt)
            if not cpt or cpt in EXCLUDE_GLOBAL or F.is_generic_concept(cpt):
                continue
            g = groups.setdefault(cpt, {"n": 0, "heat": 0, "best_rank": 99999,
                                        "leader": x["name"], "members": [], "_xs": []})
            g["n"] += 1
            g["heat"] += x.get("heat_rank") or 0
            g["members"].append((x.get("heat_rank") or 99999, x["name"]))
            g["_xs"].append(x)
            if x.get("heat_rank") and x["heat_rank"] < g["best_rank"]:
                g["best_rank"] = x["heat_rank"]
                g["leader"] = x["name"]
    stats = []
    for k, v in groups.items():
        if v["n"] < 2:
            continue
        lu = sum(1 for x in v["_xs"] if x.get("limit_up"))
        avg_chg = round(sum((pool[x["code"]].get("pop_chg") or 0) for x in v["_xs"]) / len(v["_xs"]), 1)
        strong = sum(1 for x in v["_xs"] if (x.get("chg") or 0) > 0 and (x.get("heat_growth") is None or x.get("heat_growth") >= -5))
        weak = sum(1 for x in v["_xs"] if (x.get("heat_growth") is None or x.get("heat_growth") < 0) and (x.get("chg") is None or x.get("chg") < 0))
        stats.append({"concept": k, "n": v["n"], "heat": v["heat"], "best_rank": v["best_rank"],
                      "leader": v["leader"], "members": v["members"], "strong": strong, "weak": weak,
                      "net": strong - weak, "limit_up": lu, "avg_chg": avg_chg})
    stats.sort(key=lambda g: (-g["n"], -g["heat"]))
    # 影子概念合并: 成员>=75%被更大概念覆盖 → 主线的工艺交叉影子, 剔除
    def _mem(g):
        return {nm for _, nm in g["members"]}
    shadow = set()
    for i, big in enumerate(stats):
        bm = _mem(big)
        for small in stats[i+1:]:
            if small["concept"] in shadow:
                continue
            sm = _mem(small)
            if sm and len(sm & bm) / len(sm) >= 0.75:
                shadow.add(small["concept"])
    for g in stats:
        g["shadow_of"] = next((big["concept"] for big in stats
                               if big["n"] > g["n"] and len(_mem(g) & _mem(big)) / max(1, len(_mem(g))) >= 0.75), None)
    stats = [g for g in stats if g["concept"] not in shadow]
    hot_concepts = {g["concept"] for g in stats[:8]}
    g_counts = {k: v["n"] for k, v in groups.items() if v["n"] >= 2}
    # 个股概念归属 + 龙头/杂毛系数
    for x in stocks:
        # 全量题材串(THS 异动解读原样, 用户令 2026-10-30): 展示用, 与集群归属解耦
        x["concept_all"] = pool[x["code"]].get("kw_all") or ""
        blv = pool[x["code"]].get("benefit_lv") or ""
        x["benefit_lv"] = blv
        # 利多强度系数(用户令 2026-10-30 00:21): 概念对它利多大不大, 看逻辑正文的硬动作信号
        bm = {"强": 1.06, "弱": 0.9}.get(blv, 1.0)
        my = [cpt for cpt in pool[x["code"]].get("concepts", []) if cpt in hot_concepts]
        if not my:
            # 没进热门集群, 但概念列不该空着: 用它自己的第一个标签(同花顺/龙虎榜优先),
            # 只做展示, 系数保持中性 1.0 —— 集群加持只给真在主线上抱团的票。
            alt = pool[x["code"]].get("concepts") or []
            x["concept"] = alt[0] if alt else ""
            x["concept_pos"] = None
            x["is_leader"] = False
            x["concept_factor"] = round(bm, 3)
            continue
        my.sort(key=lambda cpt: -groups[cpt]["n"])
        cpt = my[0]
        x["concept"] = cpt
        pos = [nm for _, nm in sorted(groups[cpt]["members"])]
        x["concept_pos"] = pos.index(x["name"]) + 1 if x["name"] in pos else None
        x["is_leader"] = (x["concept_pos"] == 1)
        cluster_factor = min(1.0, 0.8 + 0.04 * groups[cpt]["n"])
        base = 1.0 if x["is_leader"] else (0.9 if (x["concept_pos"] or 99) <= 3 else 0.68)
        x["cluster_factor"] = round(cluster_factor, 3)
        x["concept_factor"] = round(base * cluster_factor * bm, 3)
    return stats[:8], g_counts

def board_bonus(boards):
    """连板高度乘性加分：板越高越"妖"，但封顶（风险同步放大，不给无限加成）"""
    return {0: 1.0, 1: 1.02, 2: 1.05, 3: 1.07, 4: 1.09, 5: 1.11}.get(boards, 1.12)


def lhb_factor_of(org_net, net, chg):
    """龙虎榜资金验证：机构席位净买最硬；机构净卖且当天跌 = 出货"""
    if org_net and org_net > 0:
        return 1.03
    if org_net and org_net < 0 and (chg or 0) < 0:
        return 0.98
    if net and net > 0:
        return 1.01
    return 1.0


def mv_factor(mv):
    """流通市值软约束：低价妖股几乎都在小盘股里长出来，千亿大票人气再高也不是"妖"。
    不硬性剔除(避免规则一刀切)，只做乘性降权。mv 单位=亿元。"""
    if not mv:
        return 1.0
    if mv >= 1500:
        return 0.85
    if mv >= 500:
        return 0.92
    return 1.0


def rescore(x):
    """由 L/M + 各乘性因子重算 composite / score_final / rel_score。
    三处重算路径(composite/board_momentum/concept_logic)统一走这里，避免漏因子。"""
    if x.get("L") is None:
        return
    L, M = x["L"], x.get("M", 0)
    cf, tf = x.get("concept_factor", 1.0), x.get("t_factor", 1.0)
    pf, posf = x.get("pf") or 1.0, x.get("pos_factor", 1.0)
    bb, lf = x.get("board_bonus", 1.0), x.get("lhb_factor", 1.0)
    mf = x.get("mv_factor", 1.0)
    comp = 100 * max(0.0, L * M) ** 0.5 * cf * tf
    x["composite"] = round(min(SCORE_CAP, comp), 1)
    x["score_final"] = round(min(SCORE_CAP, comp * pf * posf * bb * lf * mf), 1)
    M_rel = max(-1.0, min(1.0, (x.get("heat_growth") or 0) / 100.0))
    x["rel_score"] = round(min(SCORE_CAP, 100 * L * M_rel * cf * tf * pf * posf * bb * lf * mf), 1)


def add_composite(stocks, total=5580):
    """综合得分 = 100 × √(L × M) × 概念系数 × 换手因子 × 价格因子 × 位置因子 × 连板因子 × 龙虎榜因子
    L = 人气排名水平分（对数标尺：第1名=1.0、第10名≈0.73、第100名≈0.46、第5580名=0）
    M = 热度增长动量分（增长 /100，封顶 1.0）

    ⚠ L 为什么用对数、而不是 1-rank/5580：池子本身就是"今天的热榜前100"，
    彼此都在同一百分位（1-rank/5580 全都 ≈0.98），对池内排序毫无区分度 —— 一换到
    "逐日排名"口径、增长普遍高企之后，整张榜会挤成一坨（实测 10 只全在 100~117 分）。
    对数标尺才能把第 10 名和第 120 名拉开。
    """
    for x in stocks:
        if x.get("heat_growth") is None or x.get("heat_rank") is None:
            continue
        rk = max(1, int(x["heat_rank"]))
        L = max(0.0, 1 - math.log10(rk) / math.log10(total))
        # 动量分用绝对标尺: 增长<=0 直接为0(排除高位滞涨股的盘中失真), +100% 及以上拿满分
        M = max(0.0, min(1.0, x["heat_growth"] / 100.0))
        chg = x.get("chg") or 0
        x["limit_up"] = chg >= 9.8 or chg >= 19.5   # 主板/双创涨停
        # 操作性只标注不降分: 高分=市场核心, 难买是执行问题不是逻辑问题
        x["L"], x["M"] = round(L, 3), round(M, 3)
        x["board_bonus"] = board_bonus(x.get("boards") or 0)
        x["lhb_factor"] = lhb_factor_of(x.get("lhb_org_net"), x.get("lhb_net"), chg)
        x["mv_factor"] = mv_factor(x.get("float_mv"))
        rescore(x)

def fetch_reasons(lb, pool, after_close):
    """上涨逻辑 = 异动解读里概念下面那段编号炒作逻辑正文(用户令 2026-10-30 00:15:
    「上涨逻辑是概念下面的这些字, 里面有同比增长/生肖行情这类厉害字」——
    不是 tag·题材串, 那个和题材列重复)。
    reason = 正文压缩行内版(编号点各取前段, 保留同比/中标/行情这类催化字眼);
    reason_detail = 全文(悬浮 title 显示, 渲染端已有)。"""
    import re as _re

    def _condense(reason):
        t = (reason or "").split("（免责声明")[0].strip()
        # "1、xxx\n2、yyy" → 每点压到 ~40 字, 用；连成一行; 行内总长 ~110 字封顶
        pts = [_re.sub(r"\s+", "", p) for p in _re.split(r"\n?\s*\d+、", t)]
        pts = [p for p in pts if p]
        if not pts:
            return t.replace("\n", " ")[:110] + ("…" if len(t) > 110 else "")
        out, used = [], 0
        for p in pts:
            piece = p[:40] + ("…" if len(p) > 40 else "")
            if used + len(piece) > 110 and out:
                out.append("…")
                break
            out.append(piece)
            used += len(piece)
        return "；".join(out)

    for x in lb:
        a = (pool.get(x["code"]) or {}).get("anomaly")
        if a:
            x["reason"] = _condense(a.get("reason"))
            x["reason_detail"] = a.get("reason") or ""
        else:
            x["reason"] = ""
            x["reason_detail"] = ""
    return lb


def apply_board_momentum(stocks, stats, prev_trend):
    """板块动量因子(用户提炼的四维度):
    进榜数量变化 + 成员排名平均变化 + 涨停数 - 昨日涨停数
    动量>0 板块上升加分, <0 下降减分(±15%封顶)"""
    for st in stats:
        prev_lu = ((prev_trend or {}).get(st["concept"]) or {}).get("lu") or 0
        st["lu_delta"] = st.get("limit_up", 0) - prev_lu
        mom = (st.get("delta") or 0) * 2 + (st.get("avg_chg") or 0) * 0.5 + st["lu_delta"] * 3
        st["mom"] = round(mom, 1)
        st["bf"] = round(1 + max(-0.15, min(0.15, mom / 100.0)), 3)
        # 题材催化(55188 明日主题): 当天该概念已有涨停家数 / 是否新题材 —— 有催化的主线才给满系数
        f = _fry_for(st["concept"])
        if f:
            boost = 1.0
            if f["limit_up"] >= 3:
                boost += 0.05
            elif f["limit_up"] >= 1:
                boost += 0.02
            if f.get("is_new"):
                boost += 0.03
            st["bf"] = round(st["bf"] * boost, 3)
            st["fry_title"] = f.get("title") or ""
            st["fry_lu"] = f.get("limit_up")
    for x in stocks:
        bf = next((st["bf"] for st in stats if st["concept"] == x.get("concept")), None)
        if bf is not None:
            x["board_factor"] = bf
            x["concept_factor"] = round(x.get("concept_factor", 1.0) * bf, 3)
            rescore(x)
    return stats


def _quote_date():
    """行情自报的最新交易日(YYYY-MM-DD)，给前端判断"今天到底有没有开市"。

    为什么不让前端猜：页面是静态的，明天打开还是这份数据，靠 `updated_at` 的日期
    只能看出"数据旧"，看不出"今天休市"还是"今天还没更新"。带上行情侧的日期后，
    前端只要比一下本地日期就能说清是哪一种 —— 休市日标"今日休市"，交易日上午标"今日未开盘"。
    """
    try:
        import trading_day as T
        days = {k: v for k, v in T.quote_day(verbose=False).items() if v}
        if days:
            return "%s-%s-%s" % (next(iter(days.values()))[0:4],
                                 next(iter(days.values()))[4:6],
                                 next(iter(days.values()))[6:8])
    except Exception:
        pass
    return None


def _upcoming_holidays(days=60):
    """未来 N 天内的法定休市日(YYYY-MM-DD)，随数据一起下发给前端。

    前端是静态页，第二天打开还是这份数据；把日历传过去，页面才能自己说清
    "今天是休市"而不是只能含糊地说"数据有点旧"。周末不用传，前端算得出来。
    """
    try:
        import trading_day as T
        base = datetime.date.today()
        return [(base + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(days)
                if (base + datetime.timedelta(days=i)).strftime("%Y%m%d") in T.HOLIDAYS]
    except Exception:
        return []


def top(stocks, key, n=5):
    s = [x for x in stocks if x.get(key) is not None]
    s.sort(key=lambda x: x[key], reverse=True)
    return s[:n]


def inject_embedded(data):
    """把本次结果写进 index.html 的 `EMBEDDED=` 常量。

    前端首屏就是 render(EMBEDDED)，fetch data.json 成功后才覆盖；所以这个常量一旦陈旧，
    打开页面第一眼看到的就是上一次手工写入的旧榜（实测曾停在 17:20 快照，涨停/连板列全空，
    分数还是旧公式算的 55.4）。这里按 data 原样注入，不做裁剪，避免兜底与 data.json 口径分叉。
    """
    try:
        html = open(INDEX_FILE, encoding="utf-8").read()
    except FileNotFoundError:
        return False
    key = "EMBEDDED="
    try:
        start = html.index(key) + len(key)
    except ValueError:
        return False
    try:
        _, end = json.JSONDecoder().raw_decode(html, start)
    except ValueError:
        return False
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if html[start:end] == blob:
        return False
    open(INDEX_FILE, "w", encoding="utf-8").write(html[:start] + blob + html[end:])
    return True


def main():
    global FRY
    hist = load_hist()
    today = datetime.date.today().strftime("%Y%m%d")
    now = datetime.datetime.now()
    after_close = now.hour > 15 or (now.hour == 15 and now.minute >= 5)
    frac = elapsed_frac()

    # 1) 原始池: 同花顺热榜前100(热度值+概念标签) ∪ 东财人气榜前100(全市场排名+逐日历史)
    #    单用同花顺会漏掉"东财热、同花顺冷"的票(如 601188 龙江交通 东财 41 名却不在同花顺前100),
    #    单用东财拿不到概念标签 —— 两个并集, 排名一律走东财(全市场口径, 才能和 5580 名次刻度对齐)
    ths = F.fetch_ths_hot(100, "hour")
    ths_d = F.fetch_ths_hot(100, "day")
    pop = F.fetch_pop_rank(100)
    if not ths and not pop:
        print("股票池为空，退出"); return
    pool = {}
    for c, v in (pop or {}).items():
        pool[c] = {"name": "", "attn": v["rank"], "attn_rank": v["rank"],
                   "heat_rank": v["rank"], "heat": None, "pop_chg": v["chg"],
                   "ths_tags": []}
    # 同花顺 hour/day 双榜: hour=近一小时热度, day=当日热度。两榜名次差就是日内加速。
    for src, key in ((ths, "ths_hour_order"), (ths_d, "ths_day_order")):
        for c, v in (src or {}).items():
            if c not in pool:
                pool[c] = {"name": "", "attn": v["rank"], "attn_rank": v["rank"],
                           "heat_rank": None, "heat": v["heat"], "pop_chg": v["chg"],
                           "ths_tags": []}
            pool[c][key] = v["rank"]
            pool[c]["ths_heat"] = v["heat"]
            pool[c]["ths_tags"] = v["tags"]
            pool[c]["board_tag"] = v["ptag"]
            pool[c]["boards"] = v["boards"]
            pool[c]["board_days"] = v["days"]
    codes = list(pool.keys())
    print("  股票池: 东财%d + 同花顺hour%d/day%d ⇒ 并集%d"
          % (len(pop or {}), len(ths or {}), len(ths_d or {}), len(codes)))
    # 55188: 题材催化 + 龙虎榜(机构净买)
    fry = F.fetch_fry_themes()
    FRY = fry
    lhb = F.fetch_lhb_board()
    for c, v in lhb.items():
        if c in pool:
            pool[c]["lhb"] = v
    print("  题材催化 %d 个 / 龙虎榜 %d 只(池内 %d)"
          % (len(fry), len(lhb), sum(1 for c in codes if "lhb" in pool[c])))

    # 1b) K 线一次并发预取(75 日) —— 位置分/换手均值/MA5方向/均线粘合 四件事全吃这一份缓存，
    #     原来是各取一次、各 151 次请求串行 + sleep，光 K 线就占掉一半时间。
    fetch_klines_concurrent(codes, POS_WINDOW + 15, workers=10)
    # 1c) 逐日人气排名历史 → 今日排名 + 前5日均排名(热度增长的基数)
    fetch_pop_hist_all(codes, pool, today)

    # 2) 全池实时价
    fetch_quotes(codes, pool)
    # 3) 位置分(近60日区间位置), 供位置因子使用
    fetch_position(codes, pool)

    pd5 = prev_trade_dates(6)
    fetch_turnover_avg(codes, pool, pd5[-1], pd5[0])

    # 4) 精选池: 实时价<10 -> MA5向上
    fcodes = [c for c in codes if pool[c].get("price") is not None and pool[c]["price"] < 10]
    fcodes = ma5up_filter(fcodes, pool)
    if fcodes:
        fetch_ma_stick(fcodes, pool)

    # 5) 收盘后写历史(全池)
    # 每次运行都写当日快照(同日覆盖, 13:00最后一次即为当日定稿)
    hist["heat"].setdefault(today, {})
    hist["attn"].setdefault(today, {})
    hist.setdefault("rate", {}).setdefault(today, {})
    for c in codes:
        if pool[c].get("heat_rank"):
            hist["heat"][today][c] = pool[c]["heat_rank"]
            hist["attn"][today][c] = pool[c]["heat_rank"]
        if pool[c].get("ths_heat"):
            hist["rate"][today][c] = pool[c]["ths_heat"]

    # 6) 组装榜单 + 综合评分(当前排名 x 热度增长)
    #    精选池是原始池的子集 ⇒ 直接引用同一批对象, 不再跑第二遍 assemble。
    #    ⚠ 必须是**同一份 dict**: 后面的 apply_* 只对 raw 跑一次就够, sel 自动同步;
    #      若复制成两份对象再各跑一次 apply_board_momentum, 概念系数会被乘两次。
    raw_stocks = assemble(codes, pool, hist, today, frac)
    _by_code = {x["code"]: x for x in raw_stocks}
    sel_stocks = [_by_code[c] for c in fcodes if c in _by_code]
    # 6) 题材归属(用户令 2026-10-29 23:29): 全池逐股拉同花顺「异动解读」——
    #    keywordList 才是真炒作题材, 原 F10/新闻/免费标签体系当轮下线。
    #    静态 txt 无风控, 8路并发一遍; 当日无异动的票概念留空(没异动=没题材, 不硬编)。
    fetch_concepts(codes, pool)
    concept_stats, attn_counts = add_concept_stats(raw_stocks, pool)
    add_composite(raw_stocks)

    # ---- 主线探测: 概念集群(与上面同一份池子, 不再单算一遍) ----
    #     ⚠ 原来 hstocks 是另造的一份轻量对象, 没有 limit_up / heat_growth ⇒ 概念总榜的
    #       「涨停」恒为 0、「净趋势」也算错(实测 机器人概念 net -1，真实是 +7)。
    #       现在直接复用 raw_stocks 的统计结果: 涨停数/净趋势都对, 也省掉第三遍 add_concept_stats。
    hpool = pool
    heat_stats, heat_counts = concept_stats, attn_counts

    # (原「概念炒作逻辑验证」已随新闻管道下线: 异动解读的概念自带当日催化,
    #  再拿新闻反查"有没有催化"是无意义的循环 —— 每个概念都天然 logic_ok。)

    # 与前一交易日对比
    pdates = sorted([d for d in hist.get("cpt", {}) if d < today])
    prev_d = pdates[-1] if pdates else None
    prev = hist.get("cpt", {}).get(prev_d, {}) if prev_d else {}
    # 口径闸(20261029 用户令: 题材改用异动解读 keywordList): 概念集群成员数/涨停数
    # 从此系统性变小, 跨口径对比会把"口径切换"误显示成"全板块退潮" —— 旧口径日一律不比。
    _CROSS = bool(prev_d and prev_d < "20261029")
    for st in concept_stats:
        st["prev_n"] = prev.get("attn", {}).get(st["concept"])
        st["delta"] = (st["n"] - st["prev_n"]) if st.get("prev_n") is not None else None
    for st in heat_stats:
        st["prev_n"] = prev.get("heat", {}).get(st["concept"])
        st["delta"] = (st["n"] - st["prev_n"]) if st.get("prev_n") is not None else None
    if _CROSS:
        for st in concept_stats + heat_stats:
            st["prev_n"] = None
            st["delta"] = None
    # 写入今日快照(每次运行覆盖, 13:00定稿) + 涨停数入档
    hist.setdefault("cpt", {})[today] = {"heat": heat_counts, "attn": attn_counts,
        "trend": {st["concept"]: {"strong": st["strong"], "weak": st["weak"],
                                  "lu": st.get("limit_up", 0)} for st in heat_stats}}
    # 板块动量因子: 上升加分/下降减分(跨口径日不比涨停数)
    prev_trend = {} if _CROSS else ((hist.get("cpt", {}).get(prev_d, {}) or {}).get("trend", {}) if prev_d else {})
    concept_stats = apply_board_momentum(raw_stocks, concept_stats, prev_trend)   # sel 同上
    # heat_stats 与 concept_stats 已是同一份, 不需要再 apply 一次(会重复乘概念系数)
    # 概念10日综合指数 = Σ(进榜数 + 2×净强弱 + 3×涨停), 兼顾梯队容量与攻击性
    # (口径闸: 只累计 20261029 之后的新口径日, 旧口径日数值偏大会污染排名)
    ci = {}
    for d in sorted(hist.get("cpt", {}).keys())[-10:]:
        if d < "20261029":
            continue
        day = hist["cpt"][d]
        for cpt, n in (day.get("heat") or {}).items():
            t = (day.get("trend") or {}).get(cpt) or {}
            net = t.get("strong", 0) - t.get("weak", 0)
            lu = t.get("lu", 0)
            ci[cpt] = ci.get(cpt, 0) + n + 2 * net + 3 * lu
    for st in concept_stats:
        st["ci"] = ci.get(st["concept"])
    for st in heat_stats:
        st["ci"] = ci.get(st["concept"])
    # 近5日概念轨迹(净强弱, 同样只取新口径日)
    traj = {}
    for d in sorted(hist.get("cpt", {}).keys())[-6:]:
        if d < "20261029":
            continue
        for cpt, t in hist["cpt"][d].get("trend", {}).items():
            traj.setdefault(cpt, []).append({"date": d, "net": t["strong"] - t["weak"]})
    for st in heat_stats:
        st["traj"] = traj.get(st["concept"], [])[-5:]
    # 🔥新王探测: 连续2日进榜只数大增 + 强弱净转正 = 具备大规模承接资金条件的"新"
    for st in heat_stats:
        st["takeover"] = bool(prev_d and st.get("delta") is not None and st["delta"] >= 3
                              and (st.get("net") or 0) > 0 and st["n"] >= 5)
        p2 = [] if _CROSS else [d for d in pdates[-2:]]
        st["streak"] = sum(1 for d in p2
                          if (st["n"] - (hist["cpt"].get(d, {}).get("heat", {}).get(st["concept"]) or 0)) >= 3)

    _seen = {}
    for x in sel_stocks + raw_stocks:
        _seen.setdefault(x["code"], x)
    # 用户令(2026-10-29 20:24): 尽量推荐 10 元以下 —— 先按"是否低价区"分桶, 桶内再按分数排。
    # 分桶而不是直接截断, 保证低价票不够时高价票能补位, 不会凑不满 10 只。
    _price_bucket = lambda x: 0 if (x.get("price") or 99) < PF_SWEET_PRICE else 1
    lb = sorted([x for x in _seen.values()
                 if (x.get("score_final") or 0) > 0 and (x.get("price") or 99) < MAX_PRICE],
                key=lambda x: (_price_bucket(x), -x["score_final"]))[:10]
    # 新闻归因: 校正概念标签为真正的上涨原因
    lb = fetch_reasons(lb, {**pool, **hpool}, after_close)
    # 相对分参考榜: 始终输出前5(允许负分, -1也比-100靠前), 无正分时页面展示
    fb = sorted([x for x in _seen.values()
                 if (x.get("price") or 99) < MAX_PRICE and x.get("rel_score") is not None],
                key=lambda x: (_price_bucket(x), -x["rel_score"]))[:5]
    if not lb:
        fb = fetch_reasons(fb, {**pool, **hpool}, after_close)
    # 全池评分依据(页面查询用)
    board_strength = F.fetch_55188_blocks(5)
    scores = [{"name": x["name"], "code": x["code"], "price": x.get("price"),
               "heat_rank": x.get("heat_rank"), "heat_growth": x.get("heat_growth"),
               "g_cross": x.get("g_cross"), "g_accel": x.get("g_accel"),
               "boards": x.get("boards"), "board_tag": x.get("board_tag"),
               "lhb_org_net": x.get("lhb_org_net"),
               "attn_growth": x.get("attn_growth"), "L": x.get("L"), "M": x.get("M"),
               "concept": x.get("concept"), "concept_all": x.get("concept_all"),
               "concept_pos": x.get("concept_pos"),
               "benefit_lv": x.get("benefit_lv"),
               "is_leader": x.get("is_leader"), "concept_factor": x.get("concept_factor"),
               "t_factor": x.get("t_factor"), "launch": x.get("launch"),
               "swap_down": x.get("swap_down"), "turnover_ratio": x.get("turnover_ratio"),
               "limit_up": x.get("limit_up"), "pf": x.get("pf"),
               "pos60": x.get("pos60"), "pos_factor": x.get("pos_factor"),
               "board_bonus": x.get("board_bonus"), "lhb_factor": x.get("lhb_factor"),
               "mv_factor": x.get("mv_factor"), "float_mv": x.get("float_mv"),
               "composite": x.get("composite"), "score_final": x.get("score_final"),
               "rel_score": x.get("rel_score")} for x in _seen.values()]
    data = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "intraday": not after_close,
        "quote_date": _quote_date(),
        "holidays": _upcoming_holidays(),
        "leaderboard": lb,
        "fallback": fb,
        "board_strength": board_strength,
        "scores": scores,
        "lists": {
            "heat_top": top(sel_stocks, "heat"),
            "heat_growth": top(sel_stocks, "heat_growth"),
            "attn_top": top(sel_stocks, "attn"),
            "attn_growth": top(sel_stocks, "attn_growth"),
            "composite": top(sel_stocks, "composite"),
        },
        "lists_raw": {
            "heat_top": top(raw_stocks, "heat"),
            "heat_growth": top(raw_stocks, "heat_growth"),
            "attn_top": top(raw_stocks, "attn"),
            "attn_growth": top(raw_stocks, "attn_growth"),
            "composite": top(raw_stocks, "composite"),
        },
        "concept_stats": concept_stats,
        "concept_stats_heat": heat_stats,
        "concept_traj": traj,
        "prev_cpt_date": prev_d,
    }
    # 只保留最近10日历史(涨跌停/排名/概念档), 更早的裁掉
    KEEP = 10
    for k in ("heat", "attn", "rate"):
        ds = sorted(hist.get(k, {}).keys())
        for d in ds[:-KEEP]:
            hist[k].pop(d, None)
    ds = sorted(hist.get("cpt", {}).keys())
    for d in ds[:-KEEP]:
        hist["cpt"].pop(d, None)
    json.dump(hist, open(HIST_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(data, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    embed = inject_embedded(data)
    print("OK", data["updated_at"], "原始:", len(raw_stocks), "精选:", len(sel_stocks),
          "盘中折算:", round(frac, 2), "| EMBEDDED", "已刷新" if embed else "无变化")

if __name__ == "__main__":
    main()
