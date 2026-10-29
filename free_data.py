# -*- coding: utf-8 -*-
"""免费数据层: 替代 iFinD MCP
1) 人气排名: 东财股吧人气榜 (排名+今日排名变化)
2) 行情/换手率: 腾讯行情
3) 历史换手: 腾讯K线成交量 × 今日换手率 折算
4) 概念标签: 东财F10核心题材
5) 个股新闻: 东财个股资讯(用于上涨归因)"""
import requests, time, re

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
_pop_session = requests.Session(); _pop_session.headers.update(UA)


def _sym(code):
    """'002249.SZ' -> 'sz002249' (腾讯)"""
    n, mk = code.split(".")
    return mk.lower() + n


def _emcode(code):
    """'002249.SZ' -> 'SZ002249' (东财)"""
    n, mk = code.split(".")
    return mk + n


def _fetch_pop_pages(topn=100):
    out, page = {}, 1
    while len(out) < topn and page < 6:
        for attempt in range(3):
            try:
                r = _pop_session.post("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                    json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                          "marketType": "", "pageNo": page, "pageSize": 100}, timeout=12)
                data = r.json().get("data") or []
                break
            except Exception:
                data = None
                time.sleep(2)
        if not data:
            break
        for it in data:
            full = it["sc"]                      # SZ000678
            code = full[2:] + "." + full[:2]
            out[code] = {"rank": it["rk"], "chg": it.get("hisRc", 0)}
        page += 1
        time.sleep(0.6)
    return out


def fetch_pop_rank(topn=100):
    """东财股吧人气榜: {code: {rank, chg}} chg=排名较昨日变化(正=上升)

    ⚠ hisRc 会偶发**整体返回 0**（抓到接口的空窗口）。一旦全 0，下游 heat_growth 全算不出来、
    整张评分榜直接清零 —— 2026-10-29 20:30 就这样跑过一次空榜。所以这里必须重抓：
    只要一整批都没有任何非 0 变化，就当作脏数据丢弃重来，最多 3 轮。"""
    out = {}
    for _ in range(3):
        out = _fetch_pop_pages(topn)
        if not out or any(v["chg"] for v in out.values()):
            return out
        time.sleep(4)
    return out


def fetch_pop_hist(code):
    """东财个股人气排名历史: [{"date": "2026-10-29", "rank": 40}, ...] 共 120 天, 今日那条盘中实时更新。

    ⚠ 为什么不直接用同花顺热榜的 hot_rank_chg: 实测该字段在盘后**整批返回 0**
    (`hour/data.txt` 100/100 全 0), 用它算增长会让 84% 的股票增长=0、评分榜几乎清零。
    东财这条给的是逐日排名, 今日那条还在动, 才是"热度增长"该用的基数。"""
    try:
        r = _pop_session.post("https://emappdata.eastmoney.com/stockrank/getHisList",
            json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                  "srcSecurityCode": _emcode(code)}, timeout=12)
        rows = r.json().get("data") or []
        return [{"date": str(x.get("calcTime") or "").replace("-", ""), "rank": x.get("rank")}
                for x in rows if x.get("rank")]
    except Exception:
        return []


def fetch_quotes_tencent(codes):
    """腾讯行情: {code: {name, price, chg, turnover, float_mv}}  换手率/流通市值为实时"""
    out = {}
    for i in range(0, len(codes), 50):
        batch = ",".join(_sym(c) for c in codes[i:i+50])
        for attempt in range(3):
            try:
                r = requests.get(f"https://qt.gtimg.cn/q={batch}", headers=UA, timeout=12)
                break
            except Exception:
                r = None
                time.sleep(2)
        if r is None:
            continue
        for line in r.text.split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            body = line.split("=", 1)[1].strip('"')
            f = body.split("~")
            if len(f) < 46 or not f[2]:
                continue
            try:
                code = f[2] + "." + ("SH" if f[2].startswith(("6", "9")) else "SZ")  # 由返回code重组
                price = float(f[3]); prev = float(f[4])
                out[code] = {
                    "name": f[1],
                    "price": price,
                    "chg": round((price - prev) / prev * 100, 2) if prev else 0.0,
                    "turnover": float(f[38]) if f[38] else None,
                    "float_mv": float(f[44]) if f[44] else None,   # 流通市值(亿)
                }
            except Exception:
                continue
        time.sleep(0.6)
    return out


def fetch_turnover_hist(code, days=6):
    """近N日成交量(手), 用于把今日换手率折算回历史各日"""
    sym = _sym(code)
    for attempt in range(3):
        try:
            r = requests.get(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                params={"symbol": sym, "scale": 240, "len": days + 15},
                headers=UA, timeout=12)
            rows = r.json() or []
            return {row["day"]: float(row["volume"]) for row in rows[-days:]}
        except Exception:
            time.sleep(2)
    return {}


def fetch_concepts_em_raw(code):
    """东财F10核心题材 → **原样**板块名列表(未过滤), 供体检/审计用"""
    sym = _emcode(code)
    for attempt in range(3):
        try:
            r = requests.get(
                f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={sym}",
                headers=UA, timeout=12)
            return [x["BOARD_NAME"] for x in r.json().get("ssbk", [])]
        except Exception:
            time.sleep(2)
    return []


def fetch_concepts_em(code):
    """东财F10核心题材 → 过滤后的概念标签列表"""
    boards = fetch_concepts_em_raw(code)
    if not boards:
        return []
    return filter_concepts(boards)


# 非"炒作概念"的板块: 行业/地域/指数/属性标签
_EXCLUDE_PAT = re.compile(r"(Ⅱ|Ⅲ|板块$|指数$|省$|市$)")
_STATIC_EXCLUDE = {
    "题材股","小盘股","大盘股","中盘股","微盘股","低价股","百元股","绩优股","亏损股",
    "最近多板","昨日涨停","昨日连板","昨日高振幅","昨日高换手","昨日触板","东方财富热股",
    "标准普尔","富时罗素","MSCI中国","深股通","沪股通","融资融券","转融券标的","机构重仓",
    "基金重仓","社保重仓","QFII重仓","券商重仓","险资重仓","深成500","上证50","上证180",
    "沪深300","中证500","创业板综","股权激励","员工持股","举牌","股权转让","回购增持",
    "预盈预增","预亏预减","破净股","高股息","同花顺漂亮100","AH股","GDR","次新股",
    "含可转债","转债标的","壳资源","央企控股","国企改革","地方国企","中特估",
    "百日新高","百日新低","连续上涨","连续下跌","创历史新高","创历史新低",
    "创月新高","创半年新高","创年新高","高送转除权","近期新高","近期新低",
}
_REGION = re.compile(r".*(省|板块)$|^(北京|上海|深圳|广州|广东|浙江|江苏|山东|四川|福建|湖南|湖北|安徽|河南|河北|陕西|辽宁|天津|重庆|苏州|杭州|宁波|无锡|珠海|佛山|东莞|青岛|济南|大连|厦门|西安|武汉|成都|长沙|郑州|合肥|福州|昆明|南昌|南宁|贵州|甘肃|新疆|西藏|海南|黑龙江|吉林|山西|内蒙古|宁夏|青海|江西|广西|宜宾|汕头|泉州|绍兴|嘉兴|台州|温州|金华|常州|南通|徐州|烟台|潍坊|淄博|临沂|唐山|保定|洛阳|襄阳|岳阳|株洲|常德|惠州|中山|江门|湛江|茂名|泰州|镇江|扬州|盐城|连云港|淮安)$")

# ── 泛词闸(2026-10-29 用户令: 「百日新高」这类泛词不属于概念)────────────────────
# 坑: 东财 F10 的 BOARD_NAME 带首尾空白('百日新高 ' 后面一个空格), 精确匹配永远打不中,
#     两处黑名单(本文件 _STATIC_EXCLUDE / update_data 的 EXCLUDE_GLOBAL)全被这样绕过。
#     所以先归一化再判, 归一化后的名字同时用于分组, 避免'百日新高'和'百日新高 '各成一个组。
_INVIS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00a0"), None)


def norm_concept(name):
    """概念名归一化: 去不可见字符、全角空格, 折叠内部空白, 去首尾空白"""
    s = str(name or "").translate(_INVIS).replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


# 技术形态/交易属性/风格/指数成分/财报事件: 按词形判, 不靠穷举
_GENERIC_PAT = re.compile(
    r"(新高$|新低$|连涨$|连跌$|涨停$|跌停$|涨停板$|连板$|多板$|触板$|一字板?$|"
    r"换手$|振幅$|涨幅$|跌幅$|"
    r"风格$|重仓$|持仓$|增持$|减持$|举牌$|"
    r"增仓$|减仓$|新进$|持股$|加仓$|"                      # 基金增仓/基金减仓/QFII新进/社保新进/大基金持股
    r"市净率$|市盈率$|市销率$|"                             # 高市净率
    r"破发|破净|破增发价|"                                  # 破发股/长期破净/破增发价股
    r"^(参股|控股|举牌)|"                                   # 参股银行/参股券商/参股保险
    r"^(权重股|趋势股|周期股|微利股|红利股|高成长股|行业龙头|龙头股|妖股|牛股)$|"
    r"^(大盘|中盘|小盘|微盘|超大盘)(成长|价值)$|"
    r"(季报|中报|年报|半年报)|"                             # 三季报增长/中报预增
    r"业绩(增长|预增|预减|改善|大增|下滑|暴增)$|"            # 业绩增长/业绩预增(55188 榜常见)
    r"^(实控人|控制权)变更$|控股股东变更$|"                  # 公司事件, 不是概念
    r"(自治区|特别行政区)$|系$|"                            # 内蒙古自治区/中船系
    r"^高股息|"
    r"^(昨日|今日)|"                                        # 昨日首板/昨日炸板/昨日打二板以上表现
    r"^(创业成份|央视50_?|上证50_?|上证180_?|深证100R?|HS300_?|AB股|中字头|宁组合|茅指数)$|"
    r"(自贸区?|特区|股权分散|股权集中|密集调研|券商金股)$|"
    r"指数$|成分股$|成份股$|标的$|概念股$|题材股$|"
    r"^HS300|^中证|^上证|^深证|^创业板|^科创|^MSCI|^富时|^标普|"
    r"预增$|预减$|预盈$|预亏$|扭亏$|首亏$|摘帽$|^(ST|\*ST))"
)

# 行业分类名(东财一级/二级, 不带Ⅱ/Ⅲ后缀的那批): 行业是行业, 不是炒作概念
# 来源: 2026-10-29 对当前股票池 415 个「通过闸门」的名字逐个过目挑出来的, 不是猜的
_INDUSTRY = {
    # 一级行业(东财/申万口径)
    "电子", "机械设备", "军工", "国防军工", "汽车", "传媒", "电力设备", "食品饮料", "计算机",
    "家用电器", "通信", "商贸零售", "农林牧渔", "交通运输", "建筑材料", "轻工制造", "医药生物",
    "基础化工", "有色金属", "社会服务", "石油石化", "建筑装饰", "银行", "公用事业", "房地产",
    "煤炭", "钢铁", "纺织服饰", "纺织服装", "环保", "美容护理", "综合", "非银金融", "证券", "保险",
    # 二级/三级行业(实测从 F10 里漏出来的)
    "通信技术", "通用设备", "元件", "半导体", "消费电子", "金属制品", "汽车零部件", "底盘与发动机系统",
    "数字芯片设计", "通信设备", "半导体材料", "通信线缆及配套", "通信网络设备及器件", "计算机设备",
    "其他计算机设备", "软件开发", "垂直应用软件", "互联网服务", "数字媒体", "其他数字媒体", "文字媒体",
    "出版", "大众出版", "影视院线", "影视动漫制作", "广告营销", "营销代理", "饮料乳品", "乳品",
    "化学制药", "化学制剂", "医疗服务", "医院", "光伏设备", "电网设备", "自动化设备", "专用设备",
    "其他专用设备", "其他通用设备", "工程机械", "工程机械器件", "能源及重型设备", "蓄电池及其他电池",
    "电池", "塑料", "合成树脂", "包装印刷", "纸包装", "包装材料", "玻璃玻纤", "玻璃制造", "玻纤制造",
    "水泥", "水泥制造", "小金属", "其他小金属", "油气资源", "天然气", "物流", "铁路运输", "铁路公路",
    "公交", "农产品加工", "其他农产品加工", "饲料", "水产饲料", "粮油加工", "磨具磨料", "家居用品",
    "其他家居用品", "休闲食品", "烘焙食品", "小家电", "厨房小家电", "制冷空调设备", "通信服务",
    "通信工程及服务", "航天航空", "消费电子零部件及组装", "塑料制品", "化学原料", "化学制品",
    "工业金属", "贵金属", "能源金属", "航空机场", "航运港口", "房地产开发", "工程建设", "装修建材",
    "电力", "燃气", "教育", "游戏", "光学光电子", "电子化学品", "军工电子", "航天装备", "航空装备",
    "船舶制造", "地面兵装", "珠宝首饰", "专业服务", "多元金融", "文化传媒", "广告包装", "造纸",
    # 二级行业/业态的另一种叫法(同样实测漏出来的)
    "一般零售", "内贸流通", "百货", "多业态零售", "零售概念", "文娱消费", "节能环保", "体育产业",
    "水利建设", "绿色电力", "炼化及贸易", "炼油化工", "线缆部件及其他", "端到端供应链服务", "膜材料",
    "被动元件", "被动元件概念", "尾气治理", "土壤修复", "生态农业", "粮食概念", "水产概念",
    "净水概念", "食品安全", "毛发医疗", "精准诊断", "创新医疗服务", "职业教育", "培训教育",
    # 55188 板块榜实测漏出来的(那源是全谱榜, 行业/地域/事件都混在里面)
    "航运", "港口", "机场航空", "民营医院", "眼科医疗", "旅游", "中船系", "金融概念",
    "高股息精选",
}

# 模式覆盖不到的整词泛词(地域/风格/形态别名)
_GENERIC_WORDS = {
    "长江三角", "长三角", "珠三角", "粤港澳", "粤港澳大湾区", "京津冀", "环渤海", "成渝特区",
    "雄安新区", "西部大开发", "东北振兴", "中部崛起", "一带一路", "科技风格", "价值风格",
    "成长风格", "百日新高", "百日新低", "近期新高", "近期新低", "创历史新高", "创历史新低",
    "历史新高", "历史新低", "年内新高", "年内新低", "均线多头", "均线空头", "多头排列",
    "深圳特区", "海南自贸", "湖北自贸", "粤港自贸", "沪企改革", "乡村振兴", "统一大市场",
    "新型城镇化", "独角兽", "创投", "退税商店", "贬值受益", "中字头", "大基金持股", "证金持股",
    "行业龙头", "微利股", "红利股", "周期股", "高成长股",
}

# 明确保留的白名单: 上面两道会误伤、但确实是炒作概念的名字(宁可显式列出, 不靠运气)
_KEEP = {
    "白酒", "酿酒概念", "中药概念", "调味品概念", "快递概念", "乳业", "预制菜概念", "社区团购",
    "免税店", "新零售", "电商概念", "跨境电商", "直播电商", "网红经济", "谷子经济",
}


def is_generic_concept(name):
    """是不是「泛词」——行业/地域/指数成分/技术形态/交易属性/财报事件/风格标签,
    统统不属于炒作概念。入参先归一化, 带空白的脏名字(如 '百日新高 ')也能被拦下。"""
    b = norm_concept(name)
    if not b:
        return True
    if b in _KEEP:
        return False
    return bool(
        b in _STATIC_EXCLUDE
        or b in _GENERIC_WORDS
        or b in _INDUSTRY
        or _GENERIC_PAT.search(b)
        or _EXCLUDE_PAT.search(b)
        or _REGION.match(b)
    )


def filter_concepts(boards):
    out, seen = [], set()
    for b in boards:
        nb = norm_concept(b)
        if is_generic_concept(nb) or nb in seen:
            continue
        seen.add(nb)
        out.append(nb)
    return out


def fetch_stock_news(code, size=6):
    """东财个股资讯: [(title, summary)]"""
    sec = ("0." if code.startswith(("0", "3")) else "1.") + code.split(".")[0]
    for attempt in range(3):
        try:
            r = requests.get(
                "https://np-listapi.eastmoney.com/comm/web/getListInfo",
                params={"cfh": 1, "client": "web", "mTypeAndCode": sec,
                        "type": 1, "pageSize": size, "pageIndex": 1},
                headers=UA, timeout=12)
            items = (r.json().get("data") or {}).get("list") or []
            return [(x.get("Art_Title", ""), x.get("Art_Content", "") or x.get("Art_Summary", ""))
                    for x in items]
        except Exception:
            time.sleep(2)
    return []


_BOARD_PAT = re.compile(r"(\d+)\s*天\s*(\d+)\s*板")


def parse_board_tag(tag):
    """同花顺 popularity_tag → (天数, 板数)。妖股高度就是这个数。

    '6天5板'/'10天7板' → (6,5)/(10,7)；'首板涨停' → (1,1)；'2天1板' → (2,1)；
    '持续上榜' 及空值 → (0,0)（不是连板，只是常年热榜）。
    """
    s = str(tag or "")
    m = _BOARD_PAT.search(s)
    if m:
        return int(m.group(1)), int(m.group(2))
    if "首板" in s:
        return 1, 1
    return 0, 0


def fetch_ths_hot(topn=100, period="hour"):
    """同花顺热榜(eq.10jqka官方API): {code: {rank, heat, chg, tags, ptag, days, boards}}

    heat=同花顺热度值(绝对值, 越大越热), chg=排名较昨日变化(盘后整批为 0, 不可用)。
    period: hour=近1小时热度榜 / day=当日热度榜。两榜名次差 = 日内加速(见 update_data)。
    """
    out = {}
    try:
        r = requests.get("https://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/%s/data.txt" % period,
                         headers={"User-Agent": UA["User-Agent"],
                                  "Referer": "https://eq.10jqka.com.cn/"}, timeout=12)
        lst = (r.json().get("data") or {}).get("stock_list") or []
        for it in lst[:topn]:
            code = it["code"]
            code = code + ("." + ("SH" if code.startswith(("6", "9")) else "SZ"))
            tag = it.get("tag") or {}
            ptag = tag.get("popularity_tag") or ""
            days, boards = parse_board_tag(ptag)
            out[code] = {"rank": it["order"], "heat": float(it.get("rate") or 0),
                         "chg": it.get("hot_rank_chg") or 0,
                         "tags": tag.get("concept_tag") or [],
                         "ptag": ptag, "days": days, "boards": boards}
    except Exception:
        pass
    return out


def fetch_fry_themes():
    """55188「明日主题」: {题材名: {rank,title,limit_up_count,chg,cum,is_new,is_hot,stocks}}

    这是唯一能直接回答「这个概念为什么在动」的源: 每个题材带一句催化剂新闻 +
    当日涨停家数 + 累计涨幅 + is_new(新题材) 。评分里当概念催化用。
    """
    try:
        r = requests.get("https://www.55188.com/sec.php?action=fry_themes",
                         headers={"User-Agent": UA["User-Agent"],
                                  "X-Requested-With": "XMLHttpRequest",
                                  "Referer": "https://www.55188.com/sec.php"}, timeout=15)
        payload = r.json().get("payload") or {}
        out = {}
        for day in payload.get("days") or []:
            for it in day.get("items") or []:
                nm = norm_concept(it.get("name"))
                if not nm or nm in out:
                    continue
                out[nm] = {"date": day.get("date"), "rank": it.get("rank"),
                           "title": it.get("title") or "", "summary": it.get("summary") or "",
                           "limit_up": it.get("limit_up_count") or 0,
                           "chg": it.get("change_pct"), "cum": it.get("cumulative_change_pct"),
                           "is_new": bool(it.get("is_new")), "is_hot": bool(it.get("is_hot")),
                           "stocks": [(s.get("code"), s.get("name"), s.get("change_pct"))
                                      for s in (it.get("stocks") or [])]}
        return out
    except Exception:
        return {}


def fetch_lhb_board():
    """55188 龙虎榜: {code: {pct,net,org,org_net,turnover,topics}}
    org_net>0 = 机构席位净买入, 是"资金验证"最硬的证据(游资榜单里混机构才是真主线)。"""
    try:
        r = requests.get("https://www.55188.com/sec.php?action=lhb_board",
                         headers={"User-Agent": UA["User-Agent"],
                                  "X-Requested-With": "XMLHttpRequest",
                                  "Referer": "https://www.55188.com/sec.php"}, timeout=15)
        rows = (r.json().get("payload") or {}).get("stocks") or []
        out = {}
        for x in rows:
            code = str(x.get("code") or "")
            if len(code) != 6:
                continue
            full = code + "." + ("SH" if code.startswith(("6", "9")) else "SZ")
            out[full] = {"pct": x.get("pct"), "net": x.get("net"), "org": x.get("org") or 0,
                         "org_net": x.get("org_net") or 0, "turnover": x.get("turnover"),
                         "topics": [norm_concept(t) for t in (x.get("topics") or []) if norm_concept(t)]}
        return out
    except Exception:
        return {}


def fetch_55188_blocks(topn=30):
    """55188板块强度榜: [{name,inc,strength,main_net}]
    ⚠ 这个源返回的是**全谱板块**(行业/地域/财报事件/交易形态混在一起), 必须过泛词闸,
      否则「银行」「三季报增长」这类会直接顶到强度榜首(2026-10-29 实测就是这样漏的)。"""
    try:
        r = requests.get("https://www.55188.com/sec.php?action=blocks",
                         headers={"User-Agent": UA["User-Agent"],
                                  "X-Requested-With": "XMLHttpRequest",
                                  "Referer": "https://www.55188.com/sec.php?forcemobile=1"}, timeout=12)
        rows = (r.json().get("rows") or [])[:topn * 4]
        out, seen = [], set()
        for x in rows:
            nb = norm_concept(x.get("name"))
            if not nb or nb in seen or is_generic_concept(nb):
                continue
            seen.add(nb)
            out.append({"name": nb, "inc": x.get("inc"), "strength": x.get("strength"),
                        "main_net": x.get("main_net")})
            if len(out) >= topn:
                break
        return out
    except Exception:
        return []


_THS_MKT = {"SH": "17", "SZ": "33"}


def fetch_ths_anomaly(code):
    """同花顺热股解析「异动解读」(ms.10jqka.com.cn/thsHotDetail 页背后的数据源):
    dq.10jqka.com.cn/fuyao/transaction_history/service/v1/get/{市场}_{代码}.txt
    —— 路径式静态文件, 无风控无 cookie, 沪=17 深=33。

    返回当日(最新一条)异动解读:
      {"keywords": [...], "reason": "...", "tag": "涨停/大涨/...", "date": "YYYY-MM-DD"}
    当日没有异动记录 → None。

    这是「真正的炒作题材」(用户令 2026-10-29 23:29): keywordList 是同花顺 AI
    从公开信息里提炼的当下炒作战场词(如「羊字辈」「机器人轴承」), 原来的
    东财F10 概念/同花顺标签/新闻归因与之相比全是死标签, 已同轮下线。"""
    mkt = _THS_MKT.get((code or "")[-2:].upper())
    if not mkt:
        return None
    try:
        r = requests.get("https://dq.10jqka.com.cn/fuyao/transaction_history/service/v1/get/%s_%s.txt"
                         % (mkt, code[:6]),
                         headers={"User-Agent": UA["User-Agent"],
                                  "Referer": "https://ms.10jqka.com.cn/"}, timeout=10)
        r.raise_for_status()
        lst = ((r.json().get("data") or {}).get("anomalyAnalysisList") or [])
        if not lst:
            return None
        a = max(lst, key=lambda e: e.get("date") or "")
        return {"keywords": a.get("keywordList") or [],
                                # 样板免责声明每条都带, 剪掉免得糊满悬浮框
                "reason": ((a.get("reason") or "").split("（免责声明")[0]).strip(),
                "tag": a.get("tagName") or "", "date": a.get("date") or ""}
    except Exception:
        return None


_THS_VP_DAYS = 30


def fetch_ths_viewpoints(code, days=_THS_VP_DAYS):
    """同花顺热股解析页「大咖观点」计数 (chat.touzime.net viewpoint_count):
    近 N 天每天投顾/机构发布的观点条数, 带 up_count/down_count 多空方向。

    返回 {"vp30": 总条数, "vp_up": 看多, "vp_down": 看空} 或 None(无数据/请求失败)。
    无风控, Python 直连即通。注意: 页面上「N位老师在线 / N人热聊中」面板是全站
    固定营销数字(任取 4 只冷热不同票全部 25/2500), 不是每股真实数据, 勿用作因子。"""
    mkt = _THS_MKT.get((code or "")[-2:].upper())
    if not mkt:
        return None
    import time as _time
    end = int(_time.time() * 1000)
    start = end - days * 86400000
    try:
        r = requests.get("https://chat.touzime.net/open-api/chat/sidebar/hotspot/v1/viewpoint_count",
                         params={"stock_code": code[:6], "stock_market": mkt,
                                 "start_time": start, "end_time": end},
                         headers={"User-Agent": UA["User-Agent"],
                                  "Referer": "https://ms.10jqka.com.cn/"}, timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("status_code") != 0:
            return None
        lst = (j.get("data") or {}).get("list") or []
        if not lst:
            return {"vp30": 0, "vp_up": 0, "vp_down": 0}
        return {"vp30": sum(e.get("count") or 0 for e in lst),
                "vp_up": sum(e.get("up_count") or 0 for e in lst),
                "vp_down": sum(e.get("down_count") or 0 for e in lst)}
    except Exception:
        return None
