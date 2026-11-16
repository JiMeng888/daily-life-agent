"""日期与节假日工具：用标准库计算日期、星期，节假日查外置数据文件。

节假日数据在 agent/holidays.json，按年份组织；每年 11 月国务院发布
次年安排后更新一次。未收录的年份返回联网检索指引，由 LLM 调
web_search 兜底，绝不允许编造。
"""
import datetime
import json
import os

# 数据文件与代码同目录
_HOLIDAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "holidays.json")
_HOLIDAY_FILE = os.path.normpath(_HOLIDAY_FILE)


def load_holidays() -> dict:
    """读取节假日数据。结构：{"2026": {"春节": {date/start/end/desc}, ...}, ...}。
    文件损坏/缺失时返回空 dict（调用方据此走联网兜底）。"""
    try:
        with open(_HOLIDAY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {y: v for y, v in data.items() if y.isdigit()}
    except (json.JSONDecodeError, OSError):
        return {}


def search_fallback(year: int, holiday: str = "") -> str:
    """生成「未收录年份」的联网兜底指引文案（给 LLM 的指令）。"""
    kw = f"{year}年放假安排"
    if holiday:
        kw += f" {holiday}"
    return (
        f"本地节假日数据未收录 {year} 年的安排（数据文件 agent/holidays.json "
        f"每年 11 月官方发布后更新一次）。请立即调用 web_search 搜索"
        f"「{kw}」，以国务院办公厅当年通知为准回答，并注明来源与检索时间；"
        "确实查不到才如实告知用户，禁止凭印象猜测放假日期。"
    )


def get_date_info(date_str: str = "") -> str:
    """返回指定日期（默认今天）的公历、星期与所在假期信息。

    参数 date_str 形如 "2026-11-11"，为空则取今天。
    """
    if date_str:
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            return f"无法解析日期 '{date_str}'，请用 YYYY-MM-DD 格式。"
    else:
        d = datetime.date.today()

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    wd = weekdays[d.weekday()]

    info = f"{d.year}年{d.month}月{d.day}日，{wd}。"

    holidays = load_holidays().get(str(d.year))
    if not holidays:
        # 未收录年份：只报基础日期，附带联网兜底指令
        return info + " " + search_fallback(d.year)

    # 精确匹配放假区间
    for name, h in holidays.items():
        try:
            start = datetime.date.fromisoformat(h["start"])
            end = datetime.date.fromisoformat(h["end"])
        except (KeyError, ValueError):
            continue
        if start <= d <= end:
            info += f" 正值{name}假期（{h.get('desc', '')}）。"
            break
    return info


if __name__ == "__main__":
    print(get_date_info())
    print(get_date_info("2027-10-03"))
    print(get_date_info("2030-01-01"))
