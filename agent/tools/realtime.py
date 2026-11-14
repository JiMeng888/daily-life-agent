"""空气质量 + 汇率工具：调用免费接口获取实时数据。"""
import json
import urllib.parse
import urllib.request

# 中文城市名 → 拼音
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


def get_air_quality(city: str) -> str:
    """查询某城市空气质量（AQI、等级、PM2.5 等）。

    使用 Open-Meteo 免费空气接口（无需 Key）。参数 city 为中文或拼音城市名。
    """
    query = CITY_ALIAS.get(city, city)
    try:
        # 先用 open-meteo 地理编码拿经纬度
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            f"name={urllib.parse.quote(query)}&count=1&language=zh"
        )
        with urllib.request.urlopen(geo_url, timeout=15) as resp:
            geo = json.loads(resp.read().decode("utf-8"))
        results = geo.get("results", [])
        if not results:
            return f"未找到城市 {city} 的空气质量数据。"
        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        name = results[0].get("name", city)

        # 拿空气质量数据
        air_url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality?"
            f"latitude={lat}&longitude={lon}&current=us_aqi,pm2_5,pm10"
        )
        with urllib.request.urlopen(air_url, timeout=15) as resp:
            air = json.loads(resp.read().decode("utf-8"))
        cur = air.get("current", {})

        aqi = cur.get("us_aqi", "?")
        pm25 = cur.get("pm2_5", "?")
        pm10 = cur.get("pm10", "?")

        # AQI 等级（中国标准简化）
        try:
            aqi_val = int(aqi)
            if aqi_val <= 50:
                level = "优"
            elif aqi_val <= 100:
                level = "良"
            elif aqi_val <= 150:
                level = "轻度污染"
            elif aqi_val <= 200:
                level = "中度污染"
            else:
                level = "重度污染"
        except (TypeError, ValueError):
            level = "未知"

        return (
            f"{name} 空气质量：AQI {aqi}（{level}），"
            f"PM2.5 {pm25} μg/m³，PM10 {pm10} μg/m³。"
        )
    except Exception as e:
        return f"空气质量查询失败：{e}。"


def get_exchange_rate(base: str, target: str) -> str:
    """查询两种货币之间的汇率。参数为 ISO 货币代码，如 USD、CNY、EUR。

    使用 frankfurter.app 免费汇率接口（无需 Key）。
    """
    try:
        url = (
            "https://api.frankfurter.app/latest?"
            f"from={urllib.parse.quote(base.upper())}&to={urllib.parse.quote(target.upper())}"
        )
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rates = data.get("rates", {})
        if target.upper() not in rates:
            return f"未找到 {base} 到 {target} 的汇率。"
        rate = rates[target.upper()]
        return f"1 {base.upper()} = {rate} {target.upper()}（{data.get('date', '')}）"
    except Exception as e:
        return f"汇率查询失败：{e}。请确认货币代码（如 USD、CNY、EUR）。"


if __name__ == "__main__":
    print(get_air_quality("北京"))
    print(get_exchange_rate("USD", "CNY"))
