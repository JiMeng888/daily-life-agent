"""E2E：真实 LLM 验证技能触发链路（use_skill → 按技能工作流回答）。

注意：数据文件写到临时目录，不污染生产 memory.json / sessions.json。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 必须在 import main 之前设置，子模块在导入时读取环境变量
_TMP = tempfile.mkdtemp(prefix="agent-e2e-")
os.environ["MEMORY_FILE"] = os.path.join(_TMP, "memory.json")
os.environ["SESSIONS_FILE"] = os.path.join(_TMP, "sessions.json")

from main import Agent  # noqa: E402

OUT = os.path.join(_TMP, "_e2e_out.txt")


def run():
    agent = Agent()
    tools_called = []
    text_parts = []
    answer = ""
    q = "帮我写个周报，这周主要做了支付接口联调、修了两个线上bug，下周要启动对账模块"
    for ev in agent.ask_stream(q):
        if ev["type"] == "tool":
            tools_called.append(ev["name"])
        elif ev["type"] == "text":
            text_parts.append(ev["content"])
        elif ev["type"] == "done":
            answer = ev["content"]

    lines = [
        f"问题: {q}",
        f"工具调用序列: {tools_called}",
        f"回答长度: {len(answer)}",
        "--- 回答 ---",
        answer,
        "--- 判定 ---",
        f"use_skill 被调用: {'use_skill' in tools_called}",
        f"回答含周报结构: {'本周' in answer and ('计划' in answer or '完成' in answer)}",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run()
