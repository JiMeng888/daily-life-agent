"""情绪调节工具：情绪识别 + 危机信号检测 + 调节建议。

配合 skills/emotion-support/SKILL.md 使用。
本工具负责「识别 + 判断」，具体的共情话术由 LLM 结合 SKILL.md 规则生成。
"""
import re

# 危机信号关键词（出现即需转介，优先级最高）
CRISIS_KEYWORDS = [
    "自杀", "自残", "不想活", "活着没意思", "一了百了", "结束生命",
    "伤害自己", "割腕", "跳楼", "想死", "活不下去", "不如死了",
]

# 情绪分类 → 常见触发词
EMOTION_KEYWORDS = {
    "焦虑": ["焦虑", "紧张", "心慌", "坐立不安", "睡不着", "失眠", "担心"],
    "愤怒": ["生气", "愤怒", "火大", "气死", "忍不了", "想骂人", "暴躁"],
    "委屈": ["委屈", "冤枉", "不被理解", "心酸", "难受"],
    "沮丧": ["沮丧", "失落", "没希望", "崩溃", "撑不住", "心累", "emo"],
    "害怕": ["害怕", "恐惧", "恐慌", "不敢", "瑟瑟发抖"],
    "内耗": ["纠结", "内耗", "反复想", "放不下", "钻牛角尖"],
    "孤独": ["孤独", "没人陪", "一个人", "寂寞"],
}


def detect_emotion(text: str) -> dict:
    """识别用户输入中的情绪信号，返回 {情绪类型, 是否危机, 命中的关键词}。

    返回 dict：
      - crisis: bool，是否为危机信号（需转介）
      - emotions: list，命中的情绪类型
      - keywords: list，命中的具体关键词
    """
    text_lower = text

    # 1. 优先检测危机信号
    crisis_hits = [k for k in CRISIS_KEYWORDS if k in text_lower]
    if crisis_hits:
        return {"crisis": True, "emotions": [], "keywords": crisis_hits}

    # 2. 检测一般情绪
    emotions = []
    keywords = []
    for emo, kws in EMOTION_KEYWORDS.items():
        hits = [k for k in kws if k in text_lower]
        if hits:
            emotions.append(emo)
            keywords.extend(hits)

    return {"crisis": False, "emotions": emotions, "keywords": keywords}


def grounding_script() -> str:
    """返回一个可选的接地/呼吸引导文本（供用户选择，不强迫）。"""
    return (
        "如果你想让自己先稳一点，可以试试 4-7-8 呼吸："
        "吸气 4 秒 → 屏住 7 秒 → 慢慢呼气 8 秒，重复 3-4 轮。"
        "同时留意一下，此刻你的肩膀和下巴是不是紧绷的？试着松开。"
    )


def crisis_resources() -> str:
    """返回危机转介资源文本。"""
    return (
        "如果这些念头让你很难受，请现在就联系能立刻帮你的人："
        "全国心理援助热线 12356，或告诉身边最信任的人，也可以就近就医。"
        "你不是一个人扛，专业的帮助能让你好过一点。"
    )


if __name__ == "__main__":
    print(detect_emotion("我最近压力好大，总是睡不着，好焦虑"))
    print(detect_emotion("有时候真的觉得活着好累，想一了百了"))
    print(detect_emotion("这个方案改了三版还被否，好崩溃"))
