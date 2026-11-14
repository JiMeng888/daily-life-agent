"""天气查询工具：调用 wttr.in 免费接口（无需 API Key）。"""
import json
import urllib.parse
import urllib.request

# 中文城市名 → 拼音（wttr.in 对拼音匹配更准）
CITY_ALIAS = {
    "深圳": "shenzhen",
    "北京": "beijing",
    "上海": "shanghai",
    "广州": "guangzhou",
    "成都": "chengdu",
    "武汉": "wuhan",
    "重庆": "chongqing",
    "昆明": "kunming",
    "杭州": "hangzhou",
    "南京": "nanjing",
}


def get_weather(city: str) -> str:
    """查询某城市当前天气，返回温度、体感、天气状况、湿度、风力等文本。

    参数 city 为城市中文名或英文名，如 "深圳" / "shenzhen"。
    """
    query = CITY_ALIAS.get(city, city)
    # 说明：wttr.in 免费接口对部分国内城市（如深圳）数据点缺失，
    # 会匹配到附近地点。若需精确数据，请换用和风天气 / OpenWeather 等正式 API，
    # 本工具的返回结构不变，只需替换下方请求逻辑。
    try:
        url = f"https://wttr.in/{urllib.parse.quote(query)}?format=j1&lang=zh"
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        cur = data.get("current_condition", [{}])[0]
        nearest = data.get("nearest_area", [{}])[0]
        area = nearest.get("areaName", [{}])[0].get("value", city)

        desc = cur.get("lang_zh", [{}])[0].get("value", "")
        # 中文描述可能为空，回退到英文描述
        if not desc:
            desc = cur.get("weatherDesc", [{}])[0].get("value", "")

        temp = cur.get("temp_C", "?")
        feels = cur.get("FeelsLikeC", "?")
        humidity = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")

        return (
            f"{area} 当前天气：{desc}，气温 {temp}°C（体感 {feels}°C），"
            f"湿度 {humidity}%，风速 {wind} km/h。"
        )
    except Exception as e:
        return f"查询失败：{e}。请确认城市名是否正确，或稍后重试。"


if __name__ == "__main__":
    print(get_weather("深圳"))
