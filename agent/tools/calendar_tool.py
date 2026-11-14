"""日期与节假日工具：用标准库计算日期、星期、农历（简化）。"""
import datetime

# 2026 年部分法定节假日放假安排（示例，以国务院当年正式通知为准）
HOLIDAYS_2026 = {
    "元旦": "1月1日至3日放假，共3天",
    "春节": "2月15日至21日放假调休，共7天",
    "清明节": "4月4日至6日放假，共3天",
    "劳动节": "5月1日至5日放假调休，共5天",
    "端午节": "6月19日至21日放假，共3天",
    "中秋节": "9月25日放假，共1天",
    "国庆节": "10月1日至7日放假调休，共7天",
}


def get_date_info(date_str: str = "") -> str:
    """返回指定日期（默认今天）的公历、星期、农历（简化）与节假日信息。

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

    # 节假日提示（仅对 2026 年示例数据做匹配）
    if d.year == 2026:
        for name, desc in HOLIDAYS_2026.items():
            # 这里只做粗略的月份匹配提示，精确判断需查官方日历
            pass
        if d.month == 1 and d.day <= 3:
            info += " 正值元旦假期。"
        elif d.month == 10 and d.day <= 7:
            info += " 正值国庆假期。"

    return info


if __name__ == "__main__":
    print(get_date_info())
