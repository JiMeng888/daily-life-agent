"""日常生活智能体的 Web UI 后端。

用 Python 标准库 http.server 提供一个本地 HTTP 服务，复用 main.py 的 Agent。

运行：py web.py
访问：http://localhost:8000

接口：
  GET  /                  → 返回前端页面（index.html）
  POST /api/chat          → 发起一轮对话，返回 JSON（含工具轨迹 + 回答）
  POST /api/memory        → 记住 / 查看长期偏好
  POST /api/conversations → 列出 / 新建 / 切换 / 删除会话
  POST /api/tasks         → 列出 / 新建 / 删除计划任务
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import Agent  # noqa: E402
from llm import _get_default_client  # noqa: E402
from memory import Memory, MEMORY_KEYS  # noqa: E402
from session_store import get_default_store  # noqa: E402
from scheduler import TaskScheduler, parse_schedule  # noqa: E402
from context import cleanup_dumps  # noqa: E402
from logger import get_logger, LOG_PATH  # noqa: E402

log = get_logger("web")

HOST = "127.0.0.1"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 全局状态：会话存储 + 每个会话对应的 Agent 实例
# 用进程内单例，保证 main.py 的提醒工具与本服务读写同一份数据
_store = get_default_store()
_agents = {}          # cid -> Agent 实例
_lock = threading.Lock()

# 全局共享的长期记忆：所有会话共用一个 Memory 实例，保证跨会话、跨重启保留
_shared_memory = Memory()


def get_agent(cid: str = "") -> Agent:
    """按会话 ID 获取（或创建）对应的 Agent，保持每个会话独立的历史。

    新建 Agent 时会把 sessions.json 里持久化的历史回灌进 agent.messages，
    这样切换会话、重启服务后，模型仍然记得这个会话的前文（修复「切会话失忆」）。
    """
    cid = cid or "default"
    with _lock:
        if cid not in _agents:
            # 传入共享 Memory，让记忆在所有会话间共享
            agent = Agent(memory=_shared_memory)
            conv = _store.get_conversation(cid)
            if conv:
                for m in conv.get("messages", []):
                    if m.get("role") in ("user", "assistant") and m.get("content"):
                        agent.messages.append({"role": m["role"], "content": m["content"]})
            _agents[cid] = agent
        return _agents[cid]


def save_messages(cid: str, agent: Agent):
    """把 Agent 的对话历史（仅 user/assistant 文本，不含 tool）存回会话。"""
    if cid in ("", "default"):
        return
    simplified = []
    for m in agent.messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            simplified.append({"role": role, "content": m.get("content", "")})
    _store.update_conversation(cid, messages=simplified)


def execute_task(task: dict, notify: bool = True) -> dict:
    """执行一个计划任务：在它专属的会话里向 Agent 提问，结果持久化。

    每个任务绑定一个独立会话（首次执行时创建，标题=任务名），
    执行结果会出现在左侧历史会话列表里，用户点进去就能看。
    notify=True 时（调度器自动触发）同时写入一条完成通知；
    手动点 ▶ 执行时前端会直接跳到结果会话，不需要再通知。
    """
    tid = task["id"]
    cid = task.get("cid")
    if not cid or not _store.get_conversation(cid):
        cid = _store.create_conversation(title=f"[任务] {task['name']}")
        _store.update_task(tid, cid=cid)

    agent = get_agent(cid)
    client = _get_default_client()
    client.reset_abort()

    answer_parts = []
    status = "ok"
    try:
        for ev in agent.ask_stream(task["prompt"]):
            if ev.get("type") == "text":
                answer_parts.append(ev.get("content", ""))
            elif ev.get("type") == "error":
                status = f"error: {ev.get('content', '')}"
    except Exception as e:
        log.exception("计划任务执行异常：%s", task.get("name"))
        status = f"error: {e}"

    answer = "".join(answer_parts)
    save_messages(cid, agent)
    _store.update_task(
        tid,
        last_run=time.time(),
        last_status=status,
    )
    if notify:
        # 任务完成通知：出现在铃铛收件箱里，点击可跳到结果会话
        summary = answer.strip().replace("\n", " ")[:80] if answer else status
        _store.add_notification(
            "task",
            f"任务「{task['name']}」执行完成",
            summary,
            cid=cid,
        )
    return {"cid": cid, "answer": answer, "status": status}


def fire_reminder(rem: dict):
    """一次性提醒到点：写入通知收件箱（不走 LLM，纯本地，零成本）。"""
    _store.add_notification("reminder", "提醒", rem.get("message", ""))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # 支持 HTTP/1.1（配合 Connection: close 保证 SSE 能正常收尾）

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        full = os.path.join(BASE_DIR, path)
        if not os.path.exists(full):
            self._send_json({"error": "页面不存在"}, status=404)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]  # 忽略查询串，支持 /?cid=xxx
        if path in ("/", "/index.html"):
            self._send_html("index.html")
        elif path == "/api/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "未找到资源"}, status=404)

    def do_POST(self):
        if self.path == "/api/chat":
            self._handle_chat()
        elif self.path == "/api/chat/stream":
            self._handle_chat_stream()
        elif self.path == "/api/chat/stop":
            self._handle_chat_stop()
        elif self.path == "/api/memory":
            self._handle_memory()
        elif self.path == "/api/context":
            self._handle_context()
        elif self.path == "/api/conversations":
            self._handle_conversations()
        elif self.path == "/api/tasks":
            self._handle_tasks()
        elif self.path == "/api/notifications":
            self._handle_notifications()
        elif self.path == "/api/reminders":
            self._handle_reminders()
        else:
            self._send_json({"error": "未找到接口"}, status=404)

    def _handle_chat(self):
        data = self._read_body()
        user_input = (data.get("message") or "").strip()
        cid = data.get("cid") or "default"
        if not user_input:
            self._send_json({"error": "消息不能为空"}, status=400)
            return

        agent = get_agent(cid)
        _get_default_client().reset_abort()  # 清空上一轮的取消标记
        events = []
        answer = ""
        try:
            for ev in agent.ask_stream(user_input):
                events.append(ev)
                if ev.get("type") == "done":
                    answer = ev.get("content", "")
        except Exception as e:
            log.exception("对话处理失败（cid=%s）", cid)
            self._send_json({"error": f"处理失败：{e}"}, status=500)
            return

        # 持久化会话历史 + 自动命名
        save_messages(cid, agent)
        if cid != "default":
            conv = _store.get_conversation(cid)
            if conv and conv.get("title") == "新对话":
                _store.update_conversation(cid, title=_store.auto_title(user_input))

        self._send_json({"answer": answer, "events": events})

    def _handle_chat_stream(self):
        """SSE 流式对话：逐条推送事件，前端用 ReadableStream 消费。

        事件格式：data: {"type": "tool"|"text"|"done"|"error", ...}\n\n
        """
        data = self._read_body()
        user_input = (data.get("message") or "").strip()
        cid = data.get("cid") or "default"
        if not user_input:
            self._send_json({"error": "消息不能为空"}, status=400)
            return

        agent = get_agent(cid)

        # 重新生成：先撤掉上一轮（提问+回答），再按同一问题重跑，避免上下文里出现两遍相同提问
        if data.get("regenerate"):
            agent.rewind_last_turn()

        # 关键：每轮开始前清空上一轮的取消标记，否则点过一次停止后会永久无法再请求
        client = _get_default_client()
        client.reset_abort()

        # 设置 SSE 响应头。
        # 必须用 Connection: close —— 因为 SSE 没有 Content-Length 也没有 chunked，
        # 客户端只能靠「连接关闭」判断流结束。若用 keep-alive，浏览器会永远等不到结尾，
        # 表现就是「回答已输出完，但按钮一直卡在停止」。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")  # 禁用代理缓冲
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        def _emit(obj):
            payload = json.dumps(obj, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        answer_parts = []
        got_terminal = False
        started = time.time()
        log.info("对话开始（cid=%s, 长度=%s）：%s", cid, len(user_input), user_input[:60])
        try:
            for ev in agent.ask_stream(user_input):
                if ev.get("type") == "text":
                    answer_parts.append(ev.get("content", ""))
                _emit(ev)
                if ev.get("type") in ("done", "error"):
                    got_terminal = True
                    break
        except (BrokenPipeError, ConnectionResetError):
            # 前端已断开（如用户关闭页面），静默处理
            got_terminal = True
            log.info("前端连接中断（cid=%s）", cid)
        except Exception as e:
            log.exception("流式对话异常（cid=%s）", cid)
            try:
                _emit({"type": "error", "content": str(e)})
            except Exception:
                pass
            got_terminal = True
        finally:
            # 兜底：无论如何都要给前端一个终结事件，避免 UI 永远卡在「生成中」
            if not got_terminal:
                try:
                    _emit({"type": "done", "content": "".join(answer_parts)})
                except Exception:
                    pass
            log.info("对话结束（cid=%s，耗时 %.1fs，回答 %s 字）",
                     cid, time.time() - started, sum(len(p) for p in answer_parts))

        # 持久化会话历史
        try:
            save_messages(cid, agent)
            if cid != "default":
                conv = _store.get_conversation(cid)
                if conv and conv.get("title") == "新对话":
                    _store.update_conversation(cid, title=_store.auto_title(user_input))
        except Exception:
            log.exception("保存会话历史失败（cid=%s）", cid)

    def _handle_chat_stop(self):
        """停止当前会话正在进行的生成。"""
        cid = self._read_body().get("cid") or "default"
        try:
            agent = get_agent(cid)
            client = _get_default_client()
            client.abort()
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_context(self):
        """上下文管理：查看统计 / 手动压缩 / 清空。"""
        data = self._read_body()
        action = data.get("action", "stats")
        cid = data.get("cid") or "default"
        agent = get_agent(cid)

        if action == "stats":
            self._send_json(agent.context_stats())
        elif action == "compress":
            self._send_json(agent.manual_compress())
        elif action == "clear":
            self._send_json(agent.clear_context())
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def _handle_memory(self):
        data = self._read_body()
        action = data.get("action", "get")
        cid = data.get("cid") or "default"
        agent = get_agent(cid)

        if action == "get":
            self._send_json({"memory": agent.memory.all(), "labels": MEMORY_KEYS})
        elif action == "set":
            key = data.get("key")
            value = data.get("value")
            if not key or not value:
                self._send_json({"error": "key 和 value 不能为空"}, status=400)
                return
            try:
                agent.remember(key, value)
            except ValueError as e:
                self._send_json({"error": str(e)}, status=400)
                return
            self._send_json({"memory": agent.memory.all(), "labels": MEMORY_KEYS})
        elif action == "delete":
            key = data.get("key")
            if not key:
                self._send_json({"error": "缺少 key"}, status=400)
                return
            deleted = agent.memory.delete(key)
            agent._build_system_prompt()  # 刷新 system prompt
            self._send_json({"deleted": deleted, "memory": agent.memory.all(), "labels": MEMORY_KEYS})
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def _handle_conversations(self):
        data = self._read_body()
        action = data.get("action", "list")

        if action == "list":
            self._send_json({"conversations": _store.list_conversations()})
        elif action == "create":
            cid = _store.create_conversation()
            self._send_json({"cid": cid})
        elif action == "delete":
            cid = data.get("cid")
            if not cid:
                self._send_json({"error": "缺少 cid"}, status=400)
                return
            _store.delete_conversation(cid)
            with _lock:
                _agents.pop(cid, None)
            self._send_json({"ok": True})
        elif action == "get":
            cid = data.get("cid")
            conv = _store.get_conversation(cid) if cid else None
            if not conv:
                self._send_json({"error": "会话不存在"}, status=404)
                return
            self._send_json({"conversation": conv})
        elif action == "rename":
            cid = data.get("cid")
            title = (data.get("title") or "").strip()
            if not cid or not title:
                self._send_json({"error": "缺少 cid 或 title"}, status=400)
                return
            if not _store.get_conversation(cid):
                self._send_json({"error": "会话不存在"}, status=404)
                return
            _store.update_conversation(cid, title=title[:40])
            self._send_json({"ok": True, "title": title[:40]})
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def _handle_tasks(self):
        data = self._read_body()
        action = data.get("action", "list")

        if action == "list":
            tasks = _store.list_tasks()
            # 标注每个任务的时间描述能否被调度器识别，前端据此提示
            for t in tasks:
                t["schedulable"] = parse_schedule(t.get("schedule", "")) is not None
            self._send_json({"tasks": tasks})
        elif action == "create":
            name = (data.get("name") or "").strip()
            schedule = (data.get("schedule") or "").strip()
            prompt = (data.get("prompt") or "").strip()
            if not name or not prompt:
                self._send_json({"error": "name 和 prompt 不能为空"}, status=400)
                return
            tid = _store.add_task(name, schedule or "手动触发", prompt)
            self._send_json({
                "tid": tid,
                "schedulable": parse_schedule(schedule) is not None,
            })
        elif action == "run":
            # 手动立即执行一次（同步执行，可能耗时几十秒）
            # 手动执行前端会直接跳到结果会话，不再额外发通知
            tid = data.get("tid")
            task = _store.get_task(tid) if tid else None
            if not task:
                self._send_json({"error": "任务不存在"}, status=404)
                return
            result = execute_task(task, notify=False)
            self._send_json(result)
        elif action == "delete":
            tid = data.get("tid")
            if not tid:
                self._send_json({"error": "缺少 tid"}, status=400)
                return
            _store.delete_task(tid)
            self._send_json({"ok": True})
        elif action == "toggle":
            tid = data.get("tid")
            enabled = _store.toggle_task(tid) if tid else False
            self._send_json({"enabled": enabled})
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def _handle_notifications(self):
        """通知收件箱：list（含未读数）/ mark_read（全部已读）。"""
        data = self._read_body()
        action = data.get("action", "list")

        if action == "list":
            self._send_json({
                "notifications": _store.list_notifications(),
                "unread": _store.unread_count(),
            })
        elif action == "mark_read":
            _store.mark_all_read()
            self._send_json({"ok": True, "unread": 0})
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def _handle_reminders(self):
        """提醒管理：list / delete（创建主要通过对话里的 set_reminder 工具）。"""
        data = self._read_body()
        action = data.get("action", "list")

        if action == "list":
            self._send_json({"reminders": _store.list_reminders()})
        elif action == "delete":
            rid = data.get("rid")
            if not rid:
                self._send_json({"error": "缺少 rid"}, status=400)
                return
            self._send_json({"deleted": _store.delete_reminder(rid)})
        else:
            self._send_json({"error": "未知 action"}, status=400)

    def log_message(self, format, *args):
        # HTTP 访问日志走 debug 级（默认不刷屏），需要排查时调 LOG_LEVEL=DEBUG
        log.debug("%s - %s", self.address_string(), format % args)


def main():
    # 启动调度器：每 30 秒检查一次，到点的计划任务自动执行、到点的提醒触发通知；
    # cleanup_fn 每天清理一次过期的工具结果落盘文件
    scheduler = TaskScheduler(_store, run_fn=execute_task, reminder_fn=fire_reminder, interval=30,
                              cleanup_fn=cleanup_dumps)
    scheduler.start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("日常生活智能体 Web UI 已启动：http://localhost:%s", PORT)
    log.info("日志目录：%s", os.path.dirname(LOG_PATH))
    print("=" * 50)
    print("日常生活智能体 Web UI 已启动")
    print(f"请在浏览器打开：http://localhost:{PORT}")
    print("按 Ctrl+C 停止")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("服务已停止（Ctrl+C）")
        print("\n已停止。")
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
