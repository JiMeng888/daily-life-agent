"""限行查询工具：本地规则表（示例）优先，未收录时引导联网检索。

设计原则：本地规则表只是「快路径」，不是能力边界。
表里没有的城市，不要直接告诉用户「查不到」，而是返回明确的下一步动作
（web_search → fetch_url 核实），由 Agent 继续把答案找出来。
"""
import datetime

# 示例限行规则（以官方实时公告为准）
RULES = {
    "北京": "工作日高峰时段 7:00-9:00、17:00-20:00，五环内按尾号轮换限行（每日两个尾号）。",
    "深圳": "外地车牌工作日早晚高峰（7:00-9:00、17:30-19:30）限行，本地粤B车牌不限行。",
    "成都": "工作日 7:30-20:00，绕城高速内按尾号轮换限行（每日两个尾号）。",
}


def get_traffic(city: str, date_str: str = "") -> str:
    """返回某城市限行规则说明。date_str 暂用于判断是否工作日。"""
    d = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    is_workday = d.weekday() < 5  # 周一至周五为工作日

    if city not in RULES:
        # 关键：不是终点，而是「转联网」的信号
        return (
            f"本地规则表未收录 {city}。请不要就此结束回答，也不要用「数据库没有」敷衍——"
            f"立即调用 web_search 检索「{city} 限行 最新规定」或「{city} 限号 尾号」，"
            f"再用 fetch_url 打开最权威的页面（优先当地交警/政府官网），"
            f"确认该市是否限行、限行时段与区域、外地车规则，然后给出结论并注明来源链接与日期。"
            f"若检索后仍无可靠信息，再如实说明并建议用户查看当地交警最新公告。"
        )

    weekday_note = "今日为工作日" if is_workday else "今日为周末/休息日"
    return f"{city}限行：{RULES[city]} {weekday_note}。请以当地交管最新公告为准。"


if __name__ == "__main__":
    print(get_traffic("深圳"))
    print(get_traffic("东莞"))
