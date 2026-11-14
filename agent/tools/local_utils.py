"""本地工具：计算器、单位换算、节假日倒计时、穿衣建议（纯函数，零接口依赖）。"""
import datetime


def calc(expression: str) -> str:
    """安全计算数学表达式，返回结果。

    支持 + - * / // % ** 及括号。参数 expression 如 "2*3+4"。
    """
    # 白名单字符，防止 eval 注入
    allowed = set("0123456789+-*/().% ")
    if not expression or any(c not in allowed for c in expression):
        return "表达式含非法字符，只支持数字和 + - * / ( ) % 等运算符。"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        # 格式化：整数去小数，浮点保留合理位数
        if isinstance(result, float) and result.is_integer():
            return f"{expression} = {int(result)}"
        return f"{expression} = {result}"
    except ZeroDivisionError:
        return "错误：除数不能为 0。"
    except Exception:
        return "表达式无法计算，请检查格式（如 2*3+4）。"


# 单位换算表：所有单位统一换算到基准单位
_UNIT_TO_BASE = {
    # 长度（基准：米）
    "米": 1.0, "m": 1.0,
    "千米": 1000.0, "公里": 1000.0, "km": 1000.0,
    "厘米": 0.01, "cm": 0.01,
    "毫米": 0.001, "mm": 0.001,
    "英尺": 0.3048, "ft": 0.3048,
    "英寸": 0.0254, "in": 0.0254,
    "英里": 1609.344, "mile": 1609.344,
    # 重量（基准：千克）
    "千克": 1.0, "公斤": 1.0, "kg": 1.0,
    "克": 0.001, "g": 0.001,
    "毫克": 0.000001, "mg": 0.000001,
    "吨": 1000.0, "t": 1000.0,
    "斤": 0.5,
    "磅": 0.45359237, "lb": 0.45359237,
    "盎司": 0.0283495, "oz": 0.0283495,
    # 温度（特殊，单独处理）
    "摄氏度": "temp_c", "°c": "temp_c", "c": "temp_c",
    "华氏度": "temp_f", "°f": "temp_f", "f": "temp_f",
}


def convert_unit(value: float, from_unit: str, to_unit: str) -> str:
    """单位换算。参数 value 数值，from_unit/to_unit 单位名。

    支持长度、重量、温度。如 convert_unit(1, "千米", "米") = 1000。
    """
    fu = from_unit.strip().lower()
    tu = to_unit.strip().lower()
    fu_cn = from_unit.strip()
    tu_cn = to_unit.strip()

    # 用中文或英文查表
    f_base = _UNIT_TO_BASE.get(fu_cn) or _UNIT_TO_BASE.get(fu)
    t_base = _UNIT_TO_BASE.get(tu_cn) or _UNIT_TO_BASE.get(tu)

    if f_base is None or t_base is None:
        return f"不支持的单位：{from_unit} 或 {to_unit}。"

    # 温度特殊处理
    if f_base == "temp_c" and t_base == "temp_c":
        return f"{value}°C"
    if f_base == "temp_f" and t_base == "temp_f":
        return f"{value}°F"
    if f_base == "temp_c" and t_base == "temp_f":
        return f"{value}°C = {value * 9 / 5 + 32}°F"
    if f_base == "temp_f" and t_base == "temp_c":
        return f"{value}°F = {(value - 32) * 5 / 9}°C"
    if f_base in ("temp_c", "temp_f") or t_base in ("temp_c", "temp_f"):
        return "温度只能与温度换算，不能与长度/重量混用。"

    result = value * f_base / t_base
    if abs(result) >= 1000 or (abs(result) < 0.001 and result != 0):
        return f"{value} {from_unit} = {result:.6g} {to_unit}"
    return f"{value} {from_unit} = {round(result, 4)} {to_unit}"


# 节假日（含农历，用简化日期近似）
_HOLIDAYS = {
    "元旦": (1, 1),
    "春节": (2, 17),       # 2026 年春节正月初一约 2/17
    "清明节": (4, 5),
    "劳动节": (5, 1),
    "端午节": (6, 19),     # 2026 年端午约 6/19
    "中秋节": (9, 25),     # 2026 年中秋约 9/25
    "国庆节": (10, 1),
}


def get_holiday_countdown(holiday: str = "春节", year: int = 0) -> str:
    """计算距某节假日的天数。参数 holiday 节日名，year 年份（默认今年）。"""
    if not year:
        year = datetime.date.today().year
    if holiday not in _HOLIDAYS:
        names = "、".join(_HOLIDAYS.keys())
        return f"暂不支持该节日，可选：{names}。"

    month, day = _HOLIDAYS[holiday]
    target = datetime.date(year, month, day)
    today = datetime.date.today()
    days = (target - today).days

    if days > 0:
        return f"距离 {year} 年{holiday}（{month}月{day}日）还有 {days} 天。"
    elif days == 0:
        return f"今天就是{holiday}！"
    else:
        # 已过，算明年的
        next_target = datetime.date(year + 1, month, day)
        days_next = (next_target - today).days
        return f"{year} 年{holiday}已过，距离 {year + 1} 年{holiday}还有 {days_next} 天。"


def get_clothing(temp_c: float, weather: str = "") -> str:
    """根据温度给出穿衣建议。参数 temp_c 摄氏温度，weather 天气描述（可选）。"""
    tips = []
    if temp_c <= 0:
        tips.append("羽绒服 / 厚棉衣，注意保暖")
    elif temp_c <= 10:
        tips.append("厚外套 / 毛衣")
    elif temp_c <= 20:
        tips.append("薄外套 / 夹克")
    elif temp_c <= 28:
        tips.append("长袖衬衫 / 单衣")
    else:
        tips.append("短袖 / 清凉夏装")

    if weather:
        w = weather.lower()
        if any(k in w for k in ("雨", "rain")):
            tips.append("建议携带雨具")
        if any(k in w for k in ("雪", "snow")):
            tips.append("注意防滑保暖")
        if any(k in w for k in ("霾", "雾", "smog", "haze")):
            tips.append("建议佩戴口罩")

    return f"气温 {temp_c}°C，穿衣建议：" + "；".join(tips) + "。"


if __name__ == "__main__":
    print(calc("2*3+4"))
    print(convert_unit(1, "千米", "米"))
    print(convert_unit(25, "摄氏度", "华氏度"))
    print(get_holiday_countdown("国庆节"))
    print(get_clothing(15, "小雨"))
