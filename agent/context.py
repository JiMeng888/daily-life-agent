"""上下文管理：四层压缩策略。

1. 工具输入卸载 —— 超大 tool 结果落盘，只留文件路径，Agent 按需读取
2. tool message 简化 —— 把历史 tool 结果压缩成一行摘要，去冗余
3. 会话总结 —— 用固定模板让 LLM 总结核心内容（非自由发挥）
4. 裁剪 —— 超出上限强制删最旧消息（保底，不依赖外部调用）

用法：
    from context import ContextManager
    cm = ContextManager(llm_client, dir="./context_cache")
    messages = cm.manage(messages)   # 每轮调用前做整套处理
"""
import json
import os
import re

# 默认工具结果落盘阈值（字符数）：超过则写文件
TOOL_DUMP_THRESHOLD = 2000
# 落盘目录
DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_cache")


def _text_len(messages) -> int:
    """估算 messages 总字符数。"""
    return sum(len((m.get("content") or "")) for m in messages)


def _estimate_tokens(messages) -> int:
    """粗略估算 token：中文约 1 字 1 token，英文约 4 字符 1 token，这里按字符/2 统一粗算。"""
    return _text_len(messages) // 2


class ContextManager:
    """四层上下文压缩的管理器。每层可独立开关、调阈值。"""

    def __init__(
        self,
        llm_chat=None,               # 会话总结需要 LLM，传入 llm.chat 之类的可调用对象
        dump_dir: str = DUMP_DIR,
        tool_dump_threshold: int = TOOL_DUMP_THRESHOLD,
        enable_tool_dump: bool = True,
        enable_tool_compact: bool = True,
        enable_summary: bool = True,
        enable_trim: bool = True,
        max_tokens: int = 16000,     # 触发总结/裁剪的软上限
        max_messages: int = 40,      # 裁剪的硬上限（消息条数）
    ):
        self.llm_chat = llm_chat
        self.dump_dir = dump_dir
        self.tool_dump_threshold = tool_dump_threshold
        self.enable_tool_dump = enable_tool_dump
        self.enable_tool_compact = enable_tool_compact
        self.enable_summary = enable_summary
        self.enable_trim = enable_trim
        self.max_tokens = max_tokens
        self.max_messages = max_messages

    # ============ ① 工具输入卸载 ============

    def maybe_dump_tool_result(self, fn_name: str, result: str) -> str:
        """超大工具结果落盘，返回给 LLM 的内容（短路径或原文）。"""
        if not self.enable_tool_dump:
            return result
        if len(result) <= self.tool_dump_threshold:
            return result
        os.makedirs(self.dump_dir, exist_ok=True)
        # 文件名用工具名 + 递增序号，避免覆盖
        idx = 1
        path = os.path.join(self.dump_dir, f"{fn_name}_{idx}.txt")
        while os.path.exists(path):
            idx += 1
            path = os.path.join(self.dump_dir, f"{fn_name}_{idx}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result)
        # 只给一个「摘要 + 路径」，让 LLM 知道详情在哪、按需读
        head = result[:200].replace("\n", " ")
        return (
            f"[工具结果已卸载到文件，避免占用上下文] 共 {len(result)} 字符。\n"
            f"文件路径：{path}\n"
            f"开头预览：{head}...\n"
            f"若需完整内容，请调用 read_file 工具读取该路径。"
        )

    def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        """按需读取落盘的文件内容（供 Agent 的 read_file 工具调用）。"""
        if not os.path.isabs(path):
            path = os.path.join(self.dump_dir, path)
        if not os.path.exists(path):
            return f"[错误] 文件不存在：{path}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if offset or limit:
                content = content[offset:offset + limit]
            return content
        except OSError as e:
            return f"[错误] 读取失败：{e}"

    # ============ ② tool message 简化 ============

    def compact_tool_messages(self, messages):
        """把历史 tool 消息的 content 压缩成一行摘要，去掉原始冗余。

        只压缩「非最新一轮」的 tool 消息；最新一轮的 tool 结果需要完整保留，
        因为 LLM 正要基于它生成回答。
        """
        if not self.enable_tool_compact:
            return messages
        # 找到最后一个 user 消息的位置，之后（含本轮）的 tool 不压缩
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        for i, m in enumerate(messages):
            if m.get("role") != "tool":
                continue
            if i > last_user_idx:
                # 最新一轮的 tool 结果，保留原文
                continue
            content = m.get("content") or ""
            # 若已被卸载成路径提示，跳过（本身就是摘要）
            if "[工具结果已卸载" in content:
                continue
            m["content"] = self._compact_one(content)
        return messages

    @staticmethod
    def _compact_one(content: str) -> str:
        """把一段 tool 结果压成一行摘要。"""
        content = (content or "").strip()
        if not content:
            return ""
        # 去换行、压缩连续空白
        flat = re.sub(r"\s+", " ", content)
        if len(flat) <= 200:
            return flat
        return flat[:200] + f"...（共 {len(flat)} 字符，历史结果已截断）"

    # ============ ③ 会话总结 ============

    # 固定模板：不是让 AI 自由发挥，而是按字段填空，防止丢关键信息
    # 注意：花括号要写成 {{ }}，否则 .format() 会把 JSON 花括号当成占位符
    SUMMARY_PROMPT_TEMPLATE = (
        "你是会话整理助手。请把下面的对话历史总结成一份结构化摘要，"
        "严格按以下模板输出，只输出 JSON，不要输出任何额外文字：\n"
        '{{"topic":"一句话概括本次对话主题",'
        '"user_goal":"用户最终想达成的目标",'
        '"key_facts":["对话中确定的关键事实/数据，逐条列出"],'
        '"pending":["尚未完成或待跟进的事项，无则空数组"],'
        '"user_prefs":["发现的用户偏好或约束，无则空数组"]}}\n\n'
        "对话历史：\n{history}"
    )

    def summarize(self, messages):
        """调用 LLM 按模板总结，返回一段可替换历史的摘要文本。失败则返回 None。"""
        if not self.llm_chat:
            return None
        # 只总结 user / assistant 的文本内容，跳过 system 和 tool 原始数据
        history_lines = []
        for m in messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = (m.get("content") or "").strip()
            if content:
                history_lines.append(f"{'用户' if role == 'user' else '助手'}: {content}")
        history = "\n".join(history_lines)
        if not history.strip():
            return None

        prompt = self.SUMMARY_PROMPT_TEMPLATE.format(history=history)
        try:
            resp = self.llm_chat([{"role": "user", "content": prompt}])
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            summary = self._extract_json(content)
            if summary is None:
                return None
            return self._render_summary(summary)
        except Exception:
            return None

    @staticmethod
    def _extract_json(text: str):
        """从 LLM 返回里提取 JSON（容错：模型可能包了 ```json 代码块）。"""
        text = text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _render_summary(s: dict) -> str:
        lines = ["[早期对话摘要]"]
        lines.append(f"主题：{s.get('topic', '')}")
        lines.append(f"用户目标：{s.get('user_goal', '')}")
        facts = s.get("key_facts") or []
        if facts:
            lines.append("关键事实：" + "；".join(str(f) for f in facts))
        pending = s.get("pending") or []
        if pending:
            lines.append("待跟进：" + "；".join(str(p) for p in pending))
        prefs = s.get("user_prefs") or []
        if prefs:
            lines.append("用户偏好：" + "；".join(str(p) for p in prefs))
        return "\n".join(lines)

    # ============ ④ 裁剪（保底） ============

    def trim(self, messages):
        """超出消息条数上限时，删最旧消息，保留 system + 最新 max_messages 条。"""
        if not self.enable_trim:
            return messages
        if len(messages) <= self.max_messages:
            return messages
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        keep = rest[-(self.max_messages - len(system)):]
        return system + keep

    # ============ 总入口 ============

    def manage(self, messages, force: bool = False):
        """每轮调用前执行整套压缩：总结 → tool 简化 → 裁剪。返回处理后的 messages。

        force=True 时强制走总结（用于用户手动点「压缩上下文」），
        否则仅在 token / 消息条数超阈值时触发总结。
        """
        # 顺序：先尝试总结（把早期对话压成摘要），再简化 tool，最后裁剪兜底
        too_long = _estimate_tokens(messages) > self.max_tokens
        too_many = len(messages) > self.max_messages
        if self.enable_summary and (force or too_long or too_many):
            summary = self.summarize(messages)
            if summary:
                messages = self._replace_early_with_summary(messages, summary)

        messages = self.compact_tool_messages(messages)
        messages = self.trim(messages)
        return messages

    def _replace_early_with_summary(self, messages, summary: str):
        """把最早的若干条 user/assistant 替换成一条摘要，保留 system 和末尾一段。"""
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        # 保留末尾 6 条（约最近 3 轮），其余压成摘要
        if len(rest) <= 6:
            return messages
        tail = rest[-6:]
        summary_msg = {"role": "user", "content": summary}
        return system + [summary_msg] + tail
