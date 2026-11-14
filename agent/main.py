"""ReAct Agent 主程序：本地运行入口，带记忆功能。

运行：python agent/main.py

记忆两层：
  1. 短期记忆 —— 会话内对话历史（Agent.messages 实例状态），支持裁剪防膨胀
  2. 长期记忆 —— 用户偏好持久化到 memory.json（跨会话保留）
"""
import datetime
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm import chat, chat_stream, chat_stream_events, _get_default_client
from memory import Memory, MEMORY_KEYS, normalize_key
from context import ContextManager
from scheduler import parse_reminder_time
from session_store import get_default_store
from skills import get_default_loader
from tools import weather, calendar_tool, traffic, realtime, local_utils, emotion, websearch

# ---- 工具注册表：LLM 能"看到"的工具清单（JSON Schema 形式）----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某城市当前天气（温度、体感、天气状况、湿度、风力）",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 深圳 / 北京"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date_info",
            "description": "查询日期、星期及节假日信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD，为空表示今天",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_traffic",
            "description": "查询某城市机动车限行规则",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 深圳 / 北京"},
                    "date_str": {"type": "string", "description": "日期 YYYY-MM-DD，可为空"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_air_quality",
            "description": "查询某城市空气质量（AQI、等级、PM2.5、PM10）",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 深圳 / 北京"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clothing",
            "description": "根据温度给出穿衣建议（可附天气描述判断是否带伞/口罩）",
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_c": {"type": "number", "description": "摄氏温度"},
                    "weather": {"type": "string", "description": "天气描述，如 小雨/晴，可为空"},
                },
                "required": ["temp_c"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_exchange_rate",
            "description": "查询两种货币汇率，如美元兑人民币",
            "parameters": {
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "基准货币代码，如 USD"},
                    "target": {"type": "string", "description": "目标货币代码，如 CNY"},
                },
                "required": ["base", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_holiday_countdown",
            "description": "计算距离某节假日还有多少天",
            "parameters": {
                "type": "object",
                "properties": {
                    "holiday": {"type": "string", "description": "节日名，如 春节/国庆节"},
                    "year": {"type": "integer", "description": "年份，默认今年"},
                },
                "required": ["holiday"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_unit",
            "description": "单位换算（长度/重量/温度），如 千米转米、摄氏度转华氏度",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "数值"},
                    "from_unit": {"type": "string", "description": "原单位，如 千米"},
                    "to_unit": {"type": "string", "description": "目标单位，如 米"},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "计算数学表达式，如 2*3+4",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学表达式"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_emotion",
            "description": "识别用户输入中的情绪信号（焦虑/愤怒/委屈/沮丧/害怕/内耗/孤独）并检测是否含危机信号（自杀/自残等需转介）",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "用户输入的文字"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "把用户明确透露的、值得长期记住的个人信息/偏好保存到长期记忆（跨会话、跨重启保留）。可记内容举例：常居城市(city)、车牌尾号(car_plate)、称呼(nickname)、居住区域、工作城市、通勤方式、饮食偏好、过敏/忌口、生日、作息习惯、兴趣爱好、家庭成员、宠物等。不要在用户没提供对应信息时臆造；同 key 会被覆盖（用于更新旧记忆）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "记忆键。标准键用英文：city/car_plate/nickname；其他可直接用中文，如 通勤方式、忌口、生日",
                    },
                    "value": {"type": "string", "description": "记忆内容，如 深圳 / 5 / 不吃香菜"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "设置一次性提醒。当用户说「提醒我 / 到时候叫我 / 别忘了」且带有时间信息时使用。when 是自然语言时间，如「10分钟后」「半小时后」「明天早上8点」「今晚7点半」「周三下午3点」「11月20日9点」。提醒会在 Web 服务运行期间到点以通知形式弹出；若用户没给时间或时间含糊，先用普通对话追问清楚再调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "自然语言时间，如 10分钟后 / 明天早上8点"},
                    "message": {"type": "string", "description": "提醒内容，如 喝水 / 出门带伞"},
                },
                "required": ["when", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "查看还未触发的提醒列表（含 id、触发时间、内容）。用户问「我有哪些提醒 / 提醒都设了什么」时使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "按 id 取消一个未触发的提醒。先用 list_reminders 找到对应 id 再调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "rid": {"type": "string", "description": "提醒的 id"},
                },
                "required": ["rid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "删除长期记忆中的一条。仅在用户明确说「忘掉 / 删除 / 别再记住」某条记忆时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "要删除的记忆键，如 city 或 通勤方式"},
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索（无需 API Key）。用于两种场景：① 本地工具/规则表未收录、返回「查不到/未收录/查询失败」时，主动上网找答案；② 用户问的东西超出内置工具范围（如某地政策、办事流程、票价、开售时间、新规）。检索词用空格分词的关键词，不要写整句口语，如「东莞 限行 最新规定」「2026 春运 火车票 开售时间」。返回标题/链接/摘要，含检索时间。摘要可能过时，关键事实需再用 fetch_url 打开页面核实。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，空格分词，如 东莞 限行 最新规定"},
                    "limit": {"type": "integer", "description": "返回条数，默认 5，最多 8"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "读取指定网页的正文文本（已去掉脚本/样式/标签）。用于在 web_search 之后打开最权威的 1-2 个页面核实事实，或用户直接给了链接。遇反爬/验证页会返回失败说明，此时换别的来源，不要反复重试同一链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "必须是 http:// 或 https:// 开头的完整链接"},
                    "max_chars": {"type": "integer", "description": "最多读取字符数，默认 3000，最多 8000"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "加载指定技能（Skill）的完整工作流说明。当用户请求命中 system prompt 中列出的某个技能的触发场景时，先调用本工具加载该技能，再严格按照技能正文中的流程与约束完成任务。name 必须来自技能目录列表，不要臆造。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名，如 daily-life-weather"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_file",
            "description": "读取某个技能附带的资源文件（references/templates 等）或项目 knowledge/ 共享知识库中的文件。仅在 use_skill 返回中列出了相关资源、且完成当前任务确实需要时才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "description": "技能名，如 daily-life-weather"},
                    "path": {
                        "type": "string",
                        "description": "相对路径：技能目录内的文件（如 references/template.md），或以 knowledge/ 开头的共享知识库文件（如 knowledge/city-alias.md）",
                    },
                },
                "required": ["skill", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "按需读取被卸载到磁盘的文件内容。仅在「工具结果被替换为『工具结果已卸载到文件』提示、且确实需要完整内容」时才调用；不要凭空猜测路径，路径必须来自前面工具返回的提示文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件完整路径"},
                    "offset": {"type": "integer", "description": "起始字符偏移，默认 0"},
                    "limit": {"type": "integer", "description": "最多读取字符数，默认 2000"},
                },
                "required": ["path"],
            },
        },
    },
]

# 工具名 → 实际可执行函数
TOOL_IMPL = {
    "get_weather": weather.get_weather,
    "get_date_info": calendar_tool.get_date_info,
    "get_traffic": traffic.get_traffic,
    "get_air_quality": realtime.get_air_quality,
    "get_clothing": local_utils.get_clothing,
    "get_exchange_rate": realtime.get_exchange_rate,
    "get_holiday_countdown": local_utils.get_holiday_countdown,
    "convert_unit": local_utils.convert_unit,
    "calc": local_utils.calc,
    "detect_emotion": emotion.detect_emotion,
    "save_memory": None,    # 占位，Agent 初始化时绑定到 self._save_memory
    "forget_memory": None,  # 占位，Agent 初始化时绑定到 self._forget_memory
    "set_reminder": None,    # 占位，Agent 初始化时绑定到 self._set_reminder
    "list_reminders": None,  # 占位，Agent 初始化时绑定到 self._list_reminders
    "cancel_reminder": None,  # 占位，Agent 初始化时绑定到 self._cancel_reminder
    "read_file": None,  # 占位，Agent 初始化时绑定到 ContextManager.read_file
    "use_skill": None,        # 占位，Agent 初始化时绑定到 self._use_skill
    "read_skill_file": None,  # 占位，Agent 初始化时绑定到 self._read_skill_file
    "web_search": websearch.web_search,
    "fetch_url": websearch.fetch_url,
}

BASE_SYSTEM_PROMPT = (
    "你是一个日常生活助手，负责回答天气、日期、限行、空气质量、穿衣、汇率、倒计时、"
    "单位换算、计算等查询。"
    "规则：1) 需要实时数据（天气、限行、空气质量、汇率）时必须调用对应工具，不得凭空编造；"
    "2) 数学计算、单位换算必须调用 calc / convert_unit 工具，不要自己心算；"
    "3) 调用工具拿到结果后，用简洁的中文组织回答；"
    "4) 回答完即可，不要重复调用工具。"
    "5) 长期记忆：当用户在对话中透露了值得长期记住的个人信息/偏好（常居城市、车牌尾号、"
    "称呼、通勤方式、饮食偏好与忌口、生日、作息、兴趣爱好、家庭成员等），"
    "应调用 save_memory 工具保存，这样以后（包括重启后）都能记得；"
    "用户明确说「忘掉 / 删除」某条记忆时，调用 forget_memory 删除。"
    "保存/删除后简短确认即可，不要啰嗦。"
    "6) 提醒：用户说「提醒我 / 到时候叫我 / 别忘了」时用 set_reminder 设置一次性提醒；"
    "用户问「我有哪些提醒」用 list_reminders；用户要取消提醒时先 list_reminders 找到 id 再用 cancel_reminder。"
    "设置成功后简短确认时间和内容即可。"
    "7) 技能：下方「可用技能」列出了已安装的第三方技能。当用户请求命中某技能的触发词/场景时，"
    "必须先调用 use_skill 加载该技能的完整工作流，再严格按其指引执行；"
    "技能提到的附带文件用 read_skill_file 读取。技能与内置工具冲突时，以技能的约束为准。"
    "8) 信息缺口必须联网补齐：任何工具返回「未收录 / 查不到 / 查询失败 / 数据库暂无」时，"
    "不要就此结束回答，应立刻用 web_search 主动检索（关键词用空格分词），"
    "再用 fetch_url 打开最权威的 1-2 个页面核实，然后给出答案。"
    "用户问的内容超出内置工具范围（政策新规、办事流程、票价、开售时间、某地规定等）时同样走这条路。"
    "回答时注明信息来源（网站名+链接）与检索日期；多个来源冲突时说明分歧并优先官方来源；"
    "确实查不到才如实说明，绝不允许编造或凭印象猜。"
    "检索要克制：同一问题最多 web_search 2-3 次、fetch_url 1-2 次，够了就停下来组织答案。"
    "另外，当用户表露出情绪（焦虑、愤怒、委屈、沮丧、害怕、内耗、孤独等）或主动求助调节时，"
    "请先调用 detect_emotion 工具识别情绪信号，再遵循以下情绪支持原则："
    "① 先接住情绪（共情、命名感受，不说教、不淡化、不有毒正能量）；"
    "② 帮用户把「事实」与「解读/最坏结果」分开；"
    "③ 只给一个「小、可逆、马上能做」的下一步，不给一堆大建议；"
    "④ 呼吸/接地等调节工具是邀请不是要求；"
    "⑤ 若 detect_emotion 返回 crisis=true，立即停止安慰，"
    "温和表达关心并转介现实求助（全国心理援助热线 12356 或身边可信任的人），不独自处理危机。"
)


class Agent:
    """带记忆 + 上下文管理的 ReAct Agent。

    短期记忆：self.messages 保留完整对话历史。
    长期记忆：self.memory 持久化用户偏好。
    上下文管理：self.ctx 负责四层压缩（工具卸载 / tool 简化 / 会话总结 / 裁剪）。
    """

    def __init__(self, memory: Memory = None, max_history: int = 20):
        self.memory = memory or Memory()
        self.max_history = max_history  # 短期记忆最多保留的 message 条数（防膨胀）
        self.ctx = ContextManager(llm_chat=chat)
        # 把 read_file 工具绑定到上下文管理器的按需读取
        TOOL_IMPL["read_file"] = self.ctx.read_file
        # 把 save_memory / forget_memory 工具绑定到本实例的保存逻辑
        TOOL_IMPL["save_memory"] = self._save_memory
        TOOL_IMPL["forget_memory"] = self._forget_memory
        # 提醒工具绑定
        TOOL_IMPL["set_reminder"] = self._set_reminder
        TOOL_IMPL["list_reminders"] = self._list_reminders
        TOOL_IMPL["cancel_reminder"] = self._cancel_reminder
        # 技能工具绑定（所有会话共享同一个加载器单例，技能文件变更自动重载）
        self.skill_loader = get_default_loader()
        TOOL_IMPL["use_skill"] = self._use_skill
        TOOL_IMPL["read_skill_file"] = self._read_skill_file
        self._build_system_prompt()

    def _save_memory(self, key: str = "", value: str = ""):
        """save_memory 工具的实现：校验后写入长期记忆，返回保存结果。"""
        key = normalize_key(key)
        try:
            self.memory.set(key, value)
        except ValueError as e:
            return f"保存失败：{e}"
        # 记忆更新后刷新 system prompt，让后续回答立即感知
        self._build_system_prompt()
        label = MEMORY_KEYS.get(key, key)
        return json.dumps({"已保存": {label: self.memory.get(key)}}, ensure_ascii=False)

    def _forget_memory(self, key: str = ""):
        """forget_memory 工具的实现：删除一条记忆。"""
        key = normalize_key(key)
        if self.memory.delete(key):
            self._build_system_prompt()
            return json.dumps({"已删除": key}, ensure_ascii=False)
        return f"没有找到这条记忆：{key}"

    def _set_reminder(self, when: str = "", message: str = ""):
        """set_reminder 工具的实现：解析自然语言时间并登记提醒。"""
        if not when or not message:
            return "缺少时间或内容，无法设置提醒。"
        ts = parse_reminder_time(when)
        if ts is None:
            return (
                f"无法理解这个时间：「{when}」。可以试试「10分钟后」「明天早上8点」"
                "「周三下午3点」「11月20日9点」这样的写法。"
            )
        store = get_default_store()
        rid = store.add_reminder(message.strip(), ts)
        dt = datetime.datetime.fromtimestamp(ts)
        return json.dumps(
            {"已设置提醒": {"时间": dt.strftime("%Y-%m-%d %H:%M"), "内容": message.strip(), "id": rid}},
            ensure_ascii=False,
        )

    def _list_reminders(self):
        """list_reminders 工具的实现：返回未触发的提醒。"""
        store = get_default_store()
        items = store.list_reminders()
        if not items:
            return "目前没有待触发的提醒。"
        out = [
            {
                "id": r["id"],
                "时间": datetime.datetime.fromtimestamp(r["trigger_at"]).strftime("%Y-%m-%d %H:%M"),
                "内容": r["message"],
            }
            for r in items
        ]
        return json.dumps({"待触发提醒": out}, ensure_ascii=False)

    def _cancel_reminder(self, rid: str = ""):
        """cancel_reminder 工具的实现：按 id 删除提醒。"""
        if not rid:
            return "缺少提醒 id。"
        store = get_default_store()
        if store.delete_reminder(rid.strip()):
            return json.dumps({"已取消提醒": rid.strip()}, ensure_ascii=False)
        return f"没有找到 id 为 {rid} 的提醒（可能已触发或已取消）。"

    def _use_skill(self, name: str = ""):
        """use_skill 工具的实现：加载技能完整工作流（Level 2 渐进披露）。"""
        return self.skill_loader.load(name)

    def _read_skill_file(self, skill: str = "", path: str = ""):
        """read_skill_file 工具的实现：读取技能附带资源或 knowledge/ 共享文件。"""
        return self.skill_loader.read_resource(skill, path)

    def _build_system_prompt(self):
        """把长期记忆 + 技能目录注入 system prompt。

        注意：只替换 system 消息本身，绝不能重置 self.messages ——
        否则对话中途保存记忆（save_memory 工具）会把整段历史清空，
        留下断裂的 tool_calls 链，下一轮请求直接被 API 判 400。
        """
        extra = self.memory.to_prompt()
        prompt = BASE_SYSTEM_PROMPT
        if extra:
            prompt += (
                "\n\n已知用户信息：\n"
                + extra
                + "\n回答时优先使用这些信息（如用户未指定城市则默认其常居城市）。"
            )
        # 注入技能目录（Level 1 渐进披露：只有 name+description，代价极小）
        catalog = self.skill_loader.catalog_prompt()
        if catalog:
            prompt += "\n\n" + catalog
        self.system_prompt = prompt
        # 保留已有的非 system 消息，只换头
        rest = [m for m in getattr(self, "messages", []) if m.get("role") != "system"]
        self.messages = [{"role": "system", "content": self.system_prompt}] + rest

    def ask(self, user_input: str, max_rounds: int = 6) -> str:
        """发起一轮对话，返回最终回答，并更新短期记忆。"""
        # 短期记忆已含 system + 历史，直接追加本轮用户输入
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(max_rounds):
            # 每轮调用前做上下文压缩（工具卸载 + tool 简化 + 总结 + 裁剪）
            self.messages = self.ctx.manage(self.messages)

            reply = chat(self.messages, tools=TOOLS)

            tool_calls = reply.get("tool_calls")
            if not tool_calls:
                # 最终答案
                answer = reply.get("content", "")
                self.messages.append(reply)
                self._trim_history()
                return answer

            # 有工具调用：把 assistant 消息（含 tool_calls）加进对话
            self.messages.append(reply)

            for call in tool_calls:
                fn_name = call["function"]["name"]
                args = json.loads(call["function"].get("arguments", "{}"))

                if fn_name in TOOL_IMPL:
                    print(f"  [工具] 调用 {fn_name}({args})")
                    result = TOOL_IMPL[fn_name](**args)
                else:
                    result = f"未知工具：{fn_name}"

                # 工具结果可能是 dict（如 detect_emotion），统一转成字符串
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False)

                # 超大工具结果卸载到磁盘，只留路径提示
                # （指令类与即时推理依据不卸载：read_file / use_skill /
                #   read_skill_file / web_search / fetch_url）
                if fn_name not in ("read_file", "use_skill", "read_skill_file",
                                   "web_search", "fetch_url"):
                    result = self.ctx.maybe_dump_tool_result(fn_name, result)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )

        return "抱歉，处理超时，请换个方式再问一次。"

    def ask_stream(self, user_input: str, max_rounds: int = 6):
        """流式版 ask：yield 事件 dict，供 UI 实时展示。

        事件类型：
          {"type": "tool", "name": 工具名, "args": 参数字典}
          {"type": "text", "content": 文本增量（真流式，逐 token）}
          {"type": "done", "content": 完整最终回答}
          {"type": "error", "content": 错误信息}

        实现说明：每轮只用「一次流式请求」，同时解析文本增量与 tool_calls 增量。
        这样比「先非流式判断 tool_calls、再流式重新生成」省一半 token 和一半延迟，
        也避免长回答把非流式请求的读超时撑爆（那正是之前「卡住」的原因）。
        """
        self.messages.append({"role": "user", "content": user_input})

        try:
            for _ in range(max_rounds):
                self.messages = self.ctx.manage(self.messages)

                text_parts = []
                tool_calls = None

                for kind, payload in chat_stream_events(self.messages, tools=TOOLS):
                    if kind == "text":
                        text_parts.append(payload)
                        yield {"type": "text", "content": payload}
                    elif kind == "tool_calls":
                        tool_calls = payload

                text = "".join(text_parts)

                if tool_calls:
                    # 模型要求调工具：记录 assistant 消息（保留它已说出的文字）
                    self.messages.append({
                        "role": "assistant",
                        "content": text or "",
                        "tool_calls": tool_calls,
                    })
                    for call in tool_calls:
                        fn_name = call["function"]["name"]
                        raw_args = call["function"].get("arguments") or "{}"
                        try:
                            args = (
                                json.loads(raw_args)
                                if isinstance(raw_args, str)
                                else (raw_args or {})
                            )
                        except json.JSONDecodeError:
                            args = {}

                        yield {"type": "tool", "name": fn_name, "args": args}

                        if fn_name in TOOL_IMPL and TOOL_IMPL[fn_name] is not None:
                            try:
                                result = TOOL_IMPL[fn_name](**args)
                            except Exception as e:
                                result = f"工具执行失败：{e}"
                        else:
                            result = f"未知工具：{fn_name}"

                        if not isinstance(result, str):
                            result = json.dumps(result, ensure_ascii=False)
                        if fn_name not in ("read_file", "use_skill", "read_skill_file",
                                           "web_search", "fetch_url"):
                            result = self.ctx.maybe_dump_tool_result(fn_name, result)

                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        })
                    continue  # 带着工具结果进入下一轮

                # 没有工具调用：这就是最终答案
                self.messages.append({"role": "assistant", "content": text})
                self._trim_history()
                yield {"type": "done", "content": text}
                return

            answer = "抱歉，任务轮次过多，请换个方式再问一次。"
            yield {"type": "done", "content": answer}
        except Exception as e:
            yield {"type": "error", "content": str(e)}
        finally:
            # 无论正常结束还是被中断，都要修复历史。
            # 被中断时可能留下「有 tool_calls 但缺 tool 响应」的残缺消息，
            # 这种历史下次请求会被 API 判为协议不合法（400）。
            self._repair_messages()

    def _repair_messages(self):
        """修复残缺的对话历史：删除「有 tool_calls 但缺少对应 tool 响应」的 assistant 消息。"""
        msgs = self.messages
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            ids = [c.get("id") for c in m["tool_calls"]]
            answered = {
                x.get("tool_call_id")
                for x in msgs[i + 1:]
                if x.get("role") == "tool"
            }
            if any(tid not in answered for tid in ids):
                # 残缺：删掉这条 assistant 及其后所有 tool 消息
                keep = msgs[:i]
                keep += [x for x in msgs[i + 1:] if x.get("role") != "tool"]
                self.messages = keep
            return

    # ---- 上下文状态与控制（供 UI 调用）----

    def context_stats(self) -> dict:
        """返回当前上下文的统计信息，供 UI 展示上下文状态。"""
        messages = self.messages
        total_chars = sum(len((m.get("content") or "")) for m in messages)
        est_tokens = total_chars // 2
        return {
            "message_count": len(messages),
            "total_chars": total_chars,
            "est_tokens": est_tokens,
            "max_messages": self.ctx.max_messages,
            "max_tokens": self.ctx.max_tokens,
            "over_limit": est_tokens > self.ctx.max_tokens or len(messages) > self.ctx.max_messages,
        }

    def manual_compress(self) -> dict:
        """手动触发整套上下文压缩（总结 + tool 简化 + 裁剪），强制总结。返回压缩前后统计。"""
        before = self.context_stats()
        self.messages = self.ctx.manage(self.messages, force=True)
        after = self.context_stats()
        return {
            "before": before,
            "after": after,
            "reduced_tokens": before["est_tokens"] - after["est_tokens"],
        }

    def clear_context(self) -> dict:
        """清空上下文（保留 system prompt 与长期记忆）。"""
        self._build_system_prompt()
        return self.context_stats()

    def _trim_history(self):
        """裁剪短期记忆：超过 max_history 条时，删掉最旧的对话（保留 system）。"""
        if len(self.messages) > self.max_history:
            # 保留第一条 system，裁剪最旧的若干条
            overflow = len(self.messages) - self.max_history
            self.messages = [self.messages[0]] + self.messages[overflow + 1:]

    # ---- 长期记忆的便捷接口 ----

    def remember(self, key: str, value):
        """记住一个长期偏好（中文标签自动归一化为标准键）。"""
        self.memory.set(normalize_key(key), value)
        self._build_system_prompt()  # 更新 system prompt

    def recall(self, key: str):
        return self.memory.get(key)


def main():
    agent = Agent()

    print("=" * 50)
    print("日常生活 ReAct Agent 已启动（带记忆）")
    print("可提问：今天深圳天气 / 今天几号 / 深圳限行吗")
    print("记忆：直接说「我常住深圳」「我平时通勤坐地铁」「忘掉我的车牌」即可，"
          "输入「记忆」可查看全部")
    print("输入 exit 或 quit 退出")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if user_input.lower() in ("exit", "quit", "退出"):
            print("再见！")
            break
        if not user_input:
            continue

        if user_input in ("你记得什么", "你有什么记忆", "记忆"):
            mem = agent.memory.all()
            if mem:
                for k, v in mem.items():
                    print(f"  - {MEMORY_KEYS.get(k, k)}: {v}")
            else:
                print("  我还没有长期记忆。")
            continue

        try:
            answer = agent.ask(user_input)
            print(f"\n助手：{answer}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
