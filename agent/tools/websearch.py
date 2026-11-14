"""联网检索工具：本地规则表覆盖不到时，让 Agent 自己去网上找答案。

设计要点（零 API Key、零第三方搜索 SDK，仅用 requests）：
  - 多引擎兜底：Bing RSS（稳定、直链）→ 360 搜索（中文政策类相关性好，但易限流）
    → Bing HTML。谁先返回可用结果用谁；
  - 结果带「检索时间 + 引擎」，便于模型在回答中标注信息时效；
  - 只返回结构化摘要（标题/链接/摘要），正文另用 fetch_url 按需读取；
  - 5 分钟内存缓存，避免重复查询把引擎打到限流；
  - 全失败时明确返回失败原因，让模型如实告知，不编造。
"""
import datetime
import html
import re
import threading
import urllib.parse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 10
MAX_SNIPPET = 200
MAX_FETCH_CHARS = 3000
CACHE_TTL = 300  # 秒

# 反爬 / 验证页特征
ANTIBOT_HINTS = ("安全验证", "请输入验证码", "人机验证", "访问验证", "captcha",
                 "Just a moment", "Access Denied", "Forbidden")

# 口语填充词：留在检索词里会把引擎带偏（「今天 东莞 限行吗」→ 天气/黄历）
STOPWORDS = {
    "今天", "明天", "昨天", "后天", "现在", "目前", "最近", "近期",
    "请问", "帮我", "查一下", "查查", "查询", "查", "搜一下", "一下",
    "吗", "呢", "啊", "吧", "呀", "的", "了", "是", "有",
    "多少", "怎么", "如何", "什么", "什么时候", "啥", "几点", "多久",
    "能不能", "可不可以", "有没有", "我", "想",
}


def _strip_tags(s: str) -> str:
    """去标签 + 反转义 + 压缩空白（把 HTML 片段变成纯文本）。"""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _fix_encoding(resp):
    """中文站点编码兜底（requests 常把 GBK 页面猜成 ISO-8859-1）。"""
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp


def _is_antibot(text: str) -> bool:
    """判断是否撞上了验证码/拦截页。"""
    head = text[:3000]
    return any(h.lower() in head.lower() for h in ANTIBOT_HINTS)


# ---------------- 引擎解析 ----------------


def _parse_bing_rss(text: str):
    """Bing RSS：item/title/link/description，干净且是直链（首选）。"""
    items = []
    for block in re.findall(r"<item>(.*?)</item>", text, re.S):
        t = re.search(r"<title>(.*?)</title>", block, re.S)
        l = re.search(r"<link>(.*?)</link>", block, re.S)
        d = re.search(r"<description>(.*?)</description>", block, re.S)
        if not (t and l):
            continue
        items.append({
            "title": html.unescape(_strip_tags(t.group(1))),
            "url": html.unescape(l.group(1).strip()),
            "snippet": _strip_tags(d.group(1)) if d else "",
        })
    return items


def _parse_so(text: str):
    """360 搜索：li.res-list，h3>a 标题与链接，.res-desc 摘要。"""
    items = []
    for block in re.findall(r'<li class="res-list.*?</li>', text, re.S):
        m = re.search(r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        title = _strip_tags(m.group(2))
        p = re.search(r'class="res-desc"[^>]*>(.*?)</p>', block, re.S)
        if title:
            items.append({
                "title": title,
                "url": html.unescape(m.group(1)),
                "snippet": _strip_tags(p.group(1)) if p else "",
            })
    return items


def _parse_bing_html(text: str):
    """Bing HTML：li.b_algo，h2>a 标题与链接，p 摘要。"""
    items = []
    for block in re.findall(r'<li class="b_algo".*?</li>', text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        title = _strip_tags(m.group(2))
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if title:
            items.append({
                "title": title,
                "url": html.unescape(m.group(1)),
                "snippet": _strip_tags(p.group(1)) if p else "",
            })
    return items


# (引擎名, URL 模板, 解析函数)；顺序即优先级
ENGINES = [
    ("Bing", "https://cn.bing.com/search?format=rss&q={q}", _parse_bing_rss),
    ("360搜索", "https://www.so.com/s?q={q}", _parse_so),
    ("Bing网页", "https://cn.bing.com/search?q={q}&setlang=zh-CN", _parse_bing_html),
]

_cache = {}          # query -> (时间戳, 结果文本)
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (datetime.datetime.now().timestamp() - hit[0]) < CACHE_TTL:
            return hit[1]
    return None


def _cache_set(key, value):
    with _cache_lock:
        if len(_cache) > 200:  # 简单防无限增长
            _cache.clear()
        _cache[key] = (datetime.datetime.now().timestamp(), value)


def _search_engine(name, url_tpl, parser, query: str, limit: int):
    """调一个引擎，返回 (items, error)。"""
    url = url_tpl.format(q=urllib.parse.quote(query))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        return [], f"{name} 请求失败（{type(e).__name__}）"
    _fix_encoding(resp)
    if resp.status_code != 200:
        return [], f"{name} 返回 HTTP {resp.status_code}"
    if _is_antibot(resp.text):
        return [], f"{name} 触发反爬验证"
    items = parser(resp.text)[:limit]
    if not items:
        return [], f"{name} 无可用结果"
    return items, ""


def _clean_query(query: str):
    """去掉口语填充词，返回 (检索用查询, 相关性关键词列表)。

    「今天 东莞 限行吗」→ ("东莞 限行", ["东莞", "限行"])
    """
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    kept = [t for t in tokens if t not in STOPWORDS]
    # 「东莞限行吗」「什么时候开售」这类没分词的串，剥掉首尾的停用词
    cleaned = []
    for t in kept:
        for w in sorted(STOPWORDS, key=len, reverse=True):
            if len(t) > len(w) + 1 and t.endswith(w):
                t = t[: -len(w)]
            if len(t) > len(w) + 1 and t.startswith(w):
                t = t[len(w):]
        if t and t not in STOPWORDS:
            cleaned.append(t)
    if not cleaned:
        cleaned = tokens  # 全是停用词就退回原样
    return " ".join(cleaned), cleaned


def _core_keyword(keywords):
    """核心词：最长的非纯数字词（「2026年 春运 火车票」→ 火车票）。

    只有命中核心词的结果才算相关，避免「2026年」这种年份词把结果带偏。
    """
    candidates = [k for k in keywords if not re.fullmatch(r"[\d年月日\-/.]+", k)]
    if not candidates:
        return ""
    return max(candidates, key=len)


def _filter(items, keywords, must="", min_hits=1):
    """按关键词命中过滤并打分，返回 ([items], 最高分)。

    must: 必须命中的核心词（防止年份/泛词把结果带偏）
    min_hits: 至少命中几个关键词（宽松档用）
    """
    if not keywords:
        return items, 0.0
    scored = []
    for it in items:
        text = (it["title"] + " " + it["snippet"]).lower()
        if must and must.lower() not in text:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits < max(1, min_hits):
            continue
        scored.append((hits / len(keywords), it))
    if not scored:
        return [], 0.0
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored], scored[0][0]


def _relevance(items, keywords, must=""):
    """严格档：必须命中核心词。"""
    return _filter(items, keywords, must, 1)


def _best_from_engines(query: str, limit: int):
    """遍历引擎，返回相关性最高的一组结果：([items], 来源名, 错误列表, 是否宽松档)。"""
    keywords = [k for k in query.split() if k]
    must = _core_keyword(keywords)
    half = max(1, (len(keywords) + 1) // 2)  # 宽松档：至少命中一半关键词
    best, best_src, best_score, errors = [], "", -1.0, []
    relaxed = False
    for name, url_tpl, parser in ENGINES:
        items, err = _search_engine(name, url_tpl, parser, query, limit)
        if not items:
            errors.append(err)
            continue
        kept, score = _filter(items, keywords, must, 1)
        if kept:
            if score > best_score:
                best, best_src, best_score, relaxed = kept, name, score, False
            if score >= 0.8:
                break
            continue
        # 严格档全落空时，退到宽松档（命中期过半关键词即可），并打上标记
        kept2, score2 = _filter(items, keywords, "", half)
        if kept2:
            errors.append(f"{name} 严格匹配无结果，已放宽")
            if score2 > best_score:
                best, best_src, best_score, relaxed = kept2, name, score2, True
    return best[:limit], best_src, errors, relaxed


def web_search(query: str = "", limit: int = 5) -> str:
    """web_search 工具的实现：多引擎检索 + 相关性择优，返回结构化结果文本。"""
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not query:
        return "缺少检索关键词。"
    try:
        limit = max(1, min(int(limit or 5), 8))
    except (TypeError, ValueError):
        limit = 5

    cached = _cache_get(f"{query}|{limit}")
    if cached:
        return cached

    # 清洗口语填充词后再检索（「今天 东莞 限行吗」→「东莞 限行」）
    clean_q, keywords = _clean_query(query)
    items, source, errors, relaxed = _best_from_engines(clean_q, limit)

    # 相关性不足时退回「核心两词」再搜一次（Bing 对长查询容易跑偏）
    if (not items or relaxed) and len(keywords) > 2:
        short_q = " ".join(keywords[:2])
        items2, source2, errors2, relaxed2 = _best_from_engines(short_q, limit)
        if items2 and (not items or (not relaxed2 and not relaxed)):
            items, source, relaxed = items2, f"{source2}（查询已收敛为「{short_q}」）", relaxed2
            errors.extend(errors2)

    if not items:
        return (
            f"联网检索失败（{'；'.join(errors) or '无结果'}）。请如实告知用户暂时查不到，"
            "不要凭猜测回答；可建议用户提供官方链接，或稍后再试。"
        )

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"检索词：{clean_q}", f"检索时间：{now}（来源：{source}）", ""]
    if relaxed:
        lines.append("注意：以下结果只是部分关键词匹配，相关性一般，必须核实后再引用。")
        lines.append("")
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}")
        lines.append(f"   链接：{it['url']}")
        if it["snippet"]:
            lines.append(f"   摘要：{it['snippet'][:MAX_SNIPPET]}")
    lines.append("")
    lines.append(
        "提示：摘要可能过时或不准确。涉及政策、价格、规则等关键事实时，"
        "用 fetch_url 打开最权威的 1-2 个页面核实；回答中必须注明来源链接与检索时间。"
    )
    result = "\n".join(lines)
    _cache_set(f"{query}|{limit}", result)
    return result


def fetch_url(url: str = "", max_chars: int = MAX_FETCH_CHARS) -> str:
    """fetch_url 工具的实现：读取网页正文文本（去脚本/样式/标签）。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return "无效链接：必须以 http:// 或 https:// 开头。"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        return f"抓取失败：{type(e).__name__}（{url}）"
    _fix_encoding(resp)

    if resp.status_code != 200:
        hint = ""
        if resp.status_code in (401, 403, 412, 429):
            hint = "（该站有反爬限制，建议换一个来源）"
        return f"抓取失败：HTTP {resp.status_code}{hint}（{url}）"
    if _is_antibot(resp.text):
        return f"该页面需要人机验证，无法直接读取，建议换一个来源（{url}）"

    text = resp.text
    title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = _strip_tags(title_m.group(1)) if title_m else ""
    body = _strip_tags(text)
    try:
        max_chars = max(500, min(int(max_chars or MAX_FETCH_CHARS), 8000))
    except (TypeError, ValueError):
        max_chars = MAX_FETCH_CHARS
    truncated = len(body) > max_chars
    body = body[:max_chars]
    head = f"标题：{title}\n链接：{resp.url}\n\n" if title else f"链接：{resp.url}\n\n"
    tail = "\n…（正文过长已截断，可调大 max_chars 或换更具体的页面）" if truncated else ""
    return head + body + tail


if __name__ == "__main__":
    print(web_search("东莞 限行 最新规定", 3))
