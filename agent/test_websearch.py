"""联网检索工具单元测试：查询清洗 / 解析 / 相关性过滤 / 路径与参数防护 / Agent 注册。

运行：py agent/test_websearch.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import websearch as ws

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


BING_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>东莞限行规定2026</title><link>https://a.example.com/x1</link>
<description>东莞目前不限行，仅部分区域限货车。</description></item>
<item><title>东莞本地宝</title><link>https://b.example.com/x2</link>
<description>限行查询</description></item>
</channel></rss>"""

SO_HTML = """<ul><li class="res-list">
<h3><a href="https://c.example.com/p">东莞 限行 时间</a></h3>
<p class="res-desc">东莞限行时间规定 2026</p></li></ul>"""

BING_HTML = """<ol><li class="b_algo">
<h2><a href="https://d.example.com/q">东莞 限行 最新</a></h2>
<p>东莞尾号限行查询</p></li></ol>"""


def main():
    print("[1] 查询清洗（去口语填充词）")
    q, kws = ws._clean_query("今天 东莞 限行吗")
    check("去「今天/吗」", q == "东莞 限行", q)
    q2, _ = ws._clean_query("什么时候开售 春运 火车票")
    check("剥离前缀停用词", "什么时候" not in q2, q2)
    q3, k3 = ws._clean_query("东莞限行")
    check("无空格短串保留", "东莞限行" in q3, q3)
    q4, _ = ws._clean_query("今天")
    check("全停用词时退回原查询", q4 == "今天", q4)

    print("[2] 核心词提取")
    check("取最长非数字词", ws._core_keyword(["2026年", "春运", "火车票"]) == "火车票")
    check("全数字时为空", ws._core_keyword(["2026年", "11月"]) == "")
    check("单个词即核心词", ws._core_keyword(["东莞"]) == "东莞")

    print("[3] 解析器")
    rss = ws._parse_bing_rss(BING_RSS)
    check("Bing RSS 解析 2 条", len(rss) == 2, str(len(rss)))
    check("RSS 标题/链接/摘要", rss[0]["title"] == "东莞限行规定2026"
          and rss[0]["url"].endswith("x1") and "不限行" in rss[0]["snippet"])
    so = ws._parse_so(SO_HTML)
    check("360 解析", len(so) == 1 and "限行" in so[0]["title"], str(so))
    bh = ws._parse_bing_html(BING_HTML)
    check("Bing HTML 解析", len(bh) == 1 and bh[0]["url"].endswith("/q"), str(bh))
    check("空 HTML 返回空列表", ws._parse_so("<html></html>") == [])

    print("[4] 相关性过滤（严/宽松档）")
    items = [
        {"title": "东莞限行规定", "url": "u1", "snippet": "限行时段"},
        {"title": "东莞旅游攻略", "url": "u2", "snippet": "景点"},
        {"title": "深圳限行", "url": "u3", "snippet": "限行"},
    ]
    kept, score = ws._filter(items, ["东莞", "限行"], "东莞", 1)
    check("严格档：核心词过滤（东莞命中 2 条）", len(kept) == 2, str(kept))
    check("严格档得分", abs(score - 1.0) < 1e-6, score)
    kept2, score2 = ws._filter(items, ["东莞", "限行"], "限行", 1)
    check("严格档：核心词「限行」只留 2 条且不含攻略",
          len(kept2) == 2 and all("旅游" not in x["title"] for x in kept2), str(kept2))
    keep0, _ = ws._filter(items, ["东莞", "限行"], "尾号", 1)
    check("严格档：核心词不在任何结果中则全丢", keep0 == [], str(keep0))
    relaxed, _ = ws._filter(items, ["东莞", "限行", "尾号", "最新"], "", 1)
    check("宽松档保留部分命中", len(relaxed) >= 2, str(len(relaxed)))
    none, s0 = ws._filter(items, ["北京"], "", 1)
    check("无命中返回空", none == [] and s0 == 0.0)

    print("[5] 参数防护")
    check("空查询给提示", "缺少" in ws.web_search(""))
    check("fetch_url 非 http 拒绝", "无效链接" in ws.fetch_url("ftp://x"))
    check("fetch_url 空值拒绝", "无效链接" in ws.fetch_url(""))
    check("antibot 识别", ws._is_antibot("<title>百度安全验证</title>") is True)
    check("正常页不误判", ws._is_antibot("<title>东莞交警</title>正文") is False)

    print("[6] 在线检索（需联网）")
    live = ws.web_search("东莞 限行", 3)
    check("真实检索有结果", ("链接：" in live) and ("检索时间：" in live), live[:80])
    check("结果含相关站名", ("东莞" in live), live[:60])
    bad = ws.web_search("asdfqwerzxcv不存在的词组合987654", 3)
    check("无结果时明确失败而非编造", ("失败" in bad or "查不到" in bad), bad[:80])

    print("[7] Agent 注册")
    from main import TOOLS, TOOL_IMPL
    names = {t["function"]["name"] for t in TOOLS}
    check("web_search 已注册", "web_search" in names)
    check("fetch_url 已注册", "fetch_url" in names)
    check("web_search 有实现", TOOL_IMPL.get("web_search") is not None)
    check("fetch_url 有实现", TOOL_IMPL.get("fetch_url") is not None)
    check("工具总数 20", len(TOOLS) == 20, str(len(TOOLS)))

    print(f"\n===== {passed} 通过 / {failed} 失败 =====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
