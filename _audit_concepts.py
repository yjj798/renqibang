# -*- coding: utf-8 -*-
"""概念名体检: 把当前股票池里所有概念名拉一遍, 逐个标判定结果与命中原因。
只读, 不写 data.json。产出 _concept_audit.json + 控制台清单。

用法: python _audit_concepts.py
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import free_data as F


def reason(name):
    b = F.norm_concept(name)
    if not b:
        return "空名"
    if b in getattr(F, "_STATIC_EXCLUDE", set()):
        return "静态表"
    if b in getattr(F, "_INDUSTRY", set()):
        return "行业词表"
    if b in getattr(F, "_GENERIC_WORDS", set()):
        return "整词表"
    m = getattr(F, "_GENERIC_PAT", None)
    if m and m.search(b):
        return "词形:" + m.search(b).group(0)
    if F._EXCLUDE_PAT.search(b):
        return "后缀:" + F._EXCLUDE_PAT.search(b).group(0)
    if F._REGION.match(b):
        return "地域"
    return ""


def main():
    d = json.load(open("data.json", encoding="utf-8"))
    codes = {}
    def put(arr):
        for s in (arr or []):
            if s.get("code") and s.get("name"):
                codes[s["code"]] = s["name"]
    put(d.get("scores")); put(d.get("leaderboard")); put(d.get("fallback"))
    for k in (d.get("lists_raw") or {}):
        put(d["lists_raw"][k])
    for k in (d.get("lists") or {}):
        put(d["lists"][k])
    print("股票池 %d 只, 开始拉 F10 概念…" % len(codes))

    counts, names = {}, set()
    for i, c in enumerate(sorted(codes), 1):
        try:
            for b in F.fetch_concepts_em_raw(c):
                nb = F.norm_concept(b)
                names.add(nb)
                counts[nb] = counts.get(nb, 0) + 1
        except Exception as e:
            print("  ERR", c, type(e).__name__)
        if i % 20 == 0:
            print("  …%d/%d" % (i, len(codes)))

    passed, blocked = [], []
    for n, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        r = reason(n) or ("PASS" if not F.is_generic_concept(n) else "拦截(未归类)")
        (passed if not F.is_generic_concept(n) else blocked).append((n, c, r))

    print("\n== 通过闸门的 %d 个概念名(按覆盖股票数) ==" % len(passed))
    for n, c, r in passed:
        print("  %-14s %3d  %s" % (n, c, r))
    print("\n== 被拦截的 %d 个 ==" % len(blocked))
    for n, c, r in blocked:
        print("  %-14s %3d  %s" % (n, c, r))

    json.dump({"passed": passed, "blocked": blocked}, open("_concept_audit.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已写 _concept_audit.json")


if __name__ == "__main__":
    main()
