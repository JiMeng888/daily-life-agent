"""E2E：真实 LLM 验证「本地无数据 → 联网自查」链路。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import Agent

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_e2e_search_out.txt")


def run():
    agent = Agent()
    q = "东莞今天限行吗"
    tools_called, answer = [], ""
    for ev in agent.ask_stream(q):
        if ev["type"] == "tool":
            tools_called.append(ev["name"])
        elif ev["type"] == "done":
            answer = ev["content"]
        elif ev["type"] == "error":
            answer = "ERROR: " + ev["content"]

    has_source = ("http" in answer) or ("来源" in answer) or ("本地宝" in answer)
    lines = [
        f"问题: {q}",
        f"工具序列: {tools_called}",
        f"触发联网: {'web_search' in tools_called}",
        f"读了网页: {'fetch_url' in tools_called}",
        f"回答带来源: {has_source}",
        "--- 回答 ---",
        answer,
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run()
