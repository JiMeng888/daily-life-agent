"""E2E：通过 Web SSE 接口验证技能在 Web 链路里触发。"""
import json
import urllib.request

OUT = r"D:\zhinengt\daily-life-agent-starter\agent\_e2e_web_out.txt"

# 显式禁用代理，避免本机请求被 http_proxy 环境变量劫持导致 502
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

req = urllib.request.Request(
    "http://127.0.0.1:8000/api/chat/stream",
    data=json.dumps({"message": "帮我写周报，这周做了官网改版和两次客户拜访"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

tools, answer = [], ""
with opener.open(req, timeout=90) as resp:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "tool":
            tools.append(ev.get("name"))
        elif ev.get("type") == "done":
            answer = ev.get("content", "")
        elif ev.get("type") == "error":
            answer = "ERROR: " + ev.get("content", "")
            break

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"工具调用序列: {tools}\n")
    f.write(f"use_skill 触发: {'use_skill' in tools}\n")
    f.write("--- 回答 ---\n" + answer[:600] + "\n")
