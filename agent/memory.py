"""长期记忆：把用户偏好/个人信息持久化到本地 JSON 文件，跨会话保留。

存储位置：agent/memory.json（可用 MEMORY_FILE 环境变量覆盖）。

设计：
- 「标准键」（如 city / car_plate / nickname）供工具逻辑直接使用（默认城市、限行判断等）；
- 其余「自定义键」（如 通勤方式 / 忌口 / 生日）由 LLM 或用户自由命名，仅注入 prompt。
- MEMORY_KEYS 不是白名单，只是常用键的中文标签表，用于展示和归一化。
"""
import json
import os
import threading

DEFAULT_MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")

# 常用记忆键 → 中文标签（展示 & 注入 prompt 用；不是白名单，自定义键也可存）
MEMORY_KEYS = {
    "city": "常居城市",
    "car_plate": "车牌尾号",
    "nickname": "称呼",
    "home_area": "居住区域",
    "work_city": "工作城市",
    "commute": "通勤方式",
    "diet": "饮食偏好",
    "allergy": "过敏/忌口",
    "birthday": "生日",
    "schedule": "作息习惯",
    "hobby": "兴趣爱好",
    "family": "家庭成员",
    "pet": "宠物",
}

# 中文标签 → 标准键（把 LLM / 用户输入的中文标签归一化，避免同义重复）
LABEL_TO_KEY = {label: key for key, label in MEMORY_KEYS.items()}

MAX_ITEMS = 50         # 最多保存的记忆条数
MAX_KEY_LEN = 30       # 键名最大长度
MAX_VALUE_LEN = 200    # 单条记忆最大长度
PROMPT_MAX_ITEMS = 25  # 注入 system prompt 时最多展示的条数（防 prompt 膨胀）

_lock = threading.Lock()


def normalize_key(key: str) -> str:
    """把中文标签归一化为标准键；未知键原样返回。"""
    key = (key or "").strip()
    return LABEL_TO_KEY.get(key, key)


class Memory:
    """长期记忆的读写封装，线程安全。"""

    def __init__(self, path: str = ""):
        self.path = path or os.environ.get("MEMORY_FILE", DEFAULT_MEMORY_FILE)
        self._data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self):
        with _lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        """写入/覆盖一条记忆。超限或非法时抛 ValueError（消息可直接展示给用户）。"""
        key = str(key).strip()
        value = str(value).strip()
        if not key or not value:
            raise ValueError("key 和 value 不能为空")
        if len(key) > MAX_KEY_LEN:
            raise ValueError(f"键名过长（最多 {MAX_KEY_LEN} 字）")
        if len(value) > MAX_VALUE_LEN:
            raise ValueError(f"内容过长（最多 {MAX_VALUE_LEN} 字）")
        if key not in self._data and len(self._data) >= MAX_ITEMS:
            raise ValueError(f"记忆已达上限（{MAX_ITEMS} 条），请先删除一些再添加")
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> bool:
        """删除一个记忆键，返回是否真的删除了。"""
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False

    def count(self) -> int:
        return len(self._data)

    def all(self) -> dict:
        return dict(self._data)

    def to_prompt(self) -> str:
        """把记忆转成可注入 system prompt 的文本（最多展示最近 PROMPT_MAX_ITEMS 条）。"""
        if not self._data:
            return ""
        items = list(self._data.items())
        shown = items[-PROMPT_MAX_ITEMS:]
        parts = [f"- {MEMORY_KEYS.get(k, k)}：{v}" for k, v in shown]
        if len(items) > PROMPT_MAX_ITEMS:
            parts.append(f"- …（另有 {len(items) - PROMPT_MAX_ITEMS} 条记忆未展示）")
        return "\n".join(parts)
