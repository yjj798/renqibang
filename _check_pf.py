# -*- coding: utf-8 -*-
"""评分回归校验（价格因子三段 + 位置因子 + 最终分一致性 + 榜单低价优先）。
   常量从源码正则读取（不 import 主程序，避免副作用），逐条断言 data.json。"""
import json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

src = open("update_data.py", encoding="utf-8").read()
def const(name):
    m = re.search(r"^%s\s*=\s*([\d.]+)" % name, src, re.M)
    if not m:
        raise SystemExit("常量缺失: " + name)
    return float(m.group(1))

MAXB, BASEB = const("PF_MAX_BONUS"), const("PF_BASE_BONUS")
SWEET, CAP, FLOOR = const("PF_SWEET_PRICE"), const("PF_CAP_PRICE"), const("PF_FLOOR_PRICE")
POSB, MAXP = const("POS_BONUS"), const("MAX_PRICE")
CAP_SCORE = const("SCORE_CAP")
print("常量: MAX_BONUS=%s BASE=%s SWEET=%s CAP=%s FLOOR=%s POS_BONUS=%s MAX_PRICE=%s"
      % (MAXB, BASEB, SWEET, CAP, FLOOR, POSB, MAXP))

def pf_exp(p):
    if not p: return 1.0
    if p < FLOOR: return 1.10
    if p < SWEET: return round(1 + BASEB + (SWEET - p) / (SWEET - FLOOR) * (MAXB - BASEB), 3)
    if p < CAP: return round(1 + (CAP - p) / (CAP - SWEET) * BASEB, 3)
    return 1.0

def posf_exp(pos):
    if pos is None: return 1.0
    if pos <= 0.35: return round(1 + POSB, 3)
    if pos <= 0.65: return round(1 + POSB / 2, 3)
    return 1.0

d = json.load(open("data.json", encoding="utf-8"))
sc = d.get("scores") or []
errs = []
for s in sc:
    p, pf, pos = s.get("price"), s.get("pf"), s.get("pos60")
    if pf is None:
        errs.append(("pf 缺失", s.get("name"))); continue
    if abs(pf - pf_exp(p)) > 6e-4:
        errs.append(("pf 不符", s.get("name"), p, pf, pf_exp(p)))
    if s.get("pos_factor") is not None and abs(s["pos_factor"] - posf_exp(pos)) > 6e-4:
        errs.append(("posf 不符", s.get("name"), pos, s["pos_factor"], posf_exp(pos)))
    if s.get("composite") is not None and s.get("score_final") is not None:
        # composite 是被 SCORE_CAP 封顶后的值; 封顶股(综合>=CAP)的真实未封顶综合分
        # 无法从输出反推, score_final 按未封顶值计算 —— 封顶股跳过乘积复算, 只查不超满分。
        capped = s["composite"] >= CAP_SCORE - 0.05
        mult = ((s.get("pos_factor") or 1.0) * (s.get("board_bonus") or 1.0)
                * (s.get("lhb_factor") or 1.0) * (s.get("mv_factor") or 1.0)
                * (s.get("op_factor") or 1.0))
        exp = round(min(CAP_SCORE, s["composite"] * pf * mult), 1)
        if not capped and abs(exp - s["score_final"]) > 0.15:
            errs.append(("score_final 不符", s.get("name"), s["composite"], pf,
                         s.get("pos_factor"), s.get("board_bonus"), s["score_final"], exp))
        if s["score_final"] > CAP_SCORE + 1e-6:
            errs.append(("超过满分", s.get("name"), s["score_final"]))
    # 综合分本身也要对得上公式: 100*√(L×M)×概念系数×换手因子（L 用对数标尺）
    if s.get("L") is not None and s.get("M") is not None and s.get("composite") is not None:
        Lexp = max(0.0, 1 - __import__("math").log10(max(1, int(s["heat_rank"]))) / __import__("math").log10(5580))
        if abs(Lexp - s["L"]) > 1.5e-3:
            errs.append(("L 不符", s.get("name"), s["heat_rank"], s["L"], round(Lexp, 3)))
        cexp = min(CAP_SCORE, 100 * (s["L"] * s["M"]) ** 0.5 * (s.get("concept_factor") or 1.0) * (s.get("t_factor") or 1.0))
        if abs(round(cexp, 1) - s["composite"]) > 0.35:
            errs.append(("composite 不符", s.get("name"), s["composite"], round(cexp, 1)))

haspos = [s for s in sc if s.get("pos60") is not None]
print("样本 %d 条 | 有位置数据 %d 条 (%.0f%%) | pf 区间 [%.3f, %.3f]"
      % (len(sc), len(haspos), 100.0 * len(haspos) / max(1, len(sc)),
         min(s["pf"] for s in sc), max(s["pf"] for s in sc)))
print("异常 %d 条" % len(errs))
for e in errs[:8]: print("   ", e)

lb, fb = d.get("leaderboard") or [], d.get("fallback") or []
print("--- 正分榜 %d 只 ---" % len(lb))
for s in lb:
    print("  %-8s %-6s 价=%-6s pf=%-6s 位置=%-6s 位置因子=%-6s 综合=%-6s 最终=%s"
          % (s.get("code"), s.get("name"), s.get("price"), s.get("pf"),
             "--" if s.get("pos60") is None else "%.0f%%" % (s["pos60"] * 100),
             s.get("pos_factor"), s.get("composite"), s.get("score_final")))
over = [x["name"] for x in lb + fb if (x.get("price") or 0) >= MAXP]
cheap = [x for x in lb if (x.get("price") or 99) < SWEET]
# 低价优先的硬断言：只要有 10 元以下的票，它就必须排在 10 元以上之前
viol = 0
seen_expensive = False
for s in lb:
    if (s.get("price") or 99) >= SWEET: seen_expensive = True
    elif seen_expensive: viol += 1
print("排序检查: 10元以下 %d/%d 只 | 低价排在贵票之后= %d 处 | 超准入价= %s"
      % (len(cheap), len(lb), viol, over or "无"))
ok = not errs and not over and viol == 0
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
