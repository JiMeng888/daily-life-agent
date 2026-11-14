"""会话、计划任务、提醒、通知的持久化存储。

会话（conversation）：多轮对话历史，可新建/切换/删除。
计划任务（scheduled task）：定时任务，如「每日早报」，供调度器读取。
提醒（reminder）：一次性提醒，到点由调度器触发为通知。
通知（notification）：任务完成 / 提醒到点等事件的收件箱，前端轮询展示。

存储位置：agent/sessions.json
"""
import json
import os
import threading
import time
import uuid

DEFAULT_STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")

_lock = threading.Lock()


class SessionStore:
    """会话 + 计划任务的本地 JSON 存储，线程安全。"""

    def __init__(self, path: str = ""):
        self.path = path or DEFAULT_STORE_FILE
        self._data = {"conversations": {}, "tasks": {}, "order": [],
                      "reminders": {}, "notifications": []}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {"conversations": {}, "tasks": {}, "order": [],
                              "reminders": {}, "notifications": []}
        self._data.setdefault("conversations", {})
        self._data.setdefault("tasks", {})
        self._data.setdefault("order", [])
        self._data.setdefault("reminders", {})
        self._data.setdefault("notifications", [])

    def _save(self):
        with _lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ---- 会话 ----

    def create_conversation(self, title: str = "新对话") -> str:
        cid = uuid.uuid4().hex[:12]
        self._data["conversations"][cid] = {
            "id": cid,
            "title": title,
            "created_at": time.time(),
            "updated_at": time.time(),
            "messages": [],  # 存储 [{role, content}] 的简化历史（不含 tool 原始数据）
        }
        self._data["order"].insert(0, cid)
        self._save()
        return cid

    def list_conversations(self):
        """按更新时间倒序返回会话摘要列表。"""
        result = []
        for cid in self._data["order"]:
            c = self._data["conversations"].get(cid)
            if c:
                result.append({
                    "id": cid,
                    "title": c.get("title", "新对话"),
                    "updated_at": c.get("updated_at", 0),
                    "message_count": len(c.get("messages", [])),
                })
        return result

    def get_conversation(self, cid: str):
        return self._data["conversations"].get(cid)

    def update_conversation(self, cid: str, title: str = None, messages=None):
        c = self._data["conversations"].get(cid)
        if not c:
            return
        if title is not None:
            c["title"] = title
        if messages is not None:
            c["messages"] = messages
        c["updated_at"] = time.time()
        self._save()

    def delete_conversation(self, cid: str):
        self._data["conversations"].pop(cid, None)
        if cid in self._data["order"]:
            self._data["order"].remove(cid)
        self._save()

    def auto_title(self, first_message: str) -> str:
        """根据首条消息生成标题（截断）。"""
        t = first_message.strip().replace("\n", " ")
        return t[:20] + ("…" if len(t) > 20 else "")

    # ---- 计划任务 ----

    def add_task(self, name: str, schedule: str, prompt: str) -> str:
        tid = uuid.uuid4().hex[:8]
        self._data["tasks"][tid] = {
            "id": tid,
            "name": name,
            "schedule": schedule,   # 人类可读描述，如 "每天 08:00"
            "prompt": prompt,       # 任务要执行的提问
            "created_at": time.time(),
            "enabled": True,
        }
        self._save()
        return tid

    def list_tasks(self):
        return list(self._data["tasks"].values())

    def get_task(self, tid: str):
        return self._data["tasks"].get(tid)

    def update_task(self, tid: str, **fields):
        """更新任务任意字段（last_run / last_run_key / last_status / cid 等）。"""
        t = self._data["tasks"].get(tid)
        if not t:
            return
        t.update(fields)
        self._save()

    def delete_task(self, tid: str):
        self._data["tasks"].pop(tid, None)
        self._save()

    def toggle_task(self, tid: str) -> bool:
        t = self._data["tasks"].get(tid)
        if not t:
            return False
        t["enabled"] = not t.get("enabled", True)
        self._save()
        return t["enabled"]

    # ---- 提醒（一次性）----

    def add_reminder(self, message: str, trigger_at: float) -> str:
        rid = uuid.uuid4().hex[:8]
        self._data["reminders"][rid] = {
            "id": rid,
            "message": message,
            "trigger_at": trigger_at,   # Unix 时间戳，到点触发
            "created_at": time.time(),
            "done": False,
        }
        self._save()
        return rid

    def list_reminders(self, include_done: bool = False):
        items = [r for r in self._data["reminders"].values()
                 if include_done or not r.get("done")]
        return sorted(items, key=lambda r: r.get("trigger_at", 0))

    def due_reminders(self, now_ts: float):
        return [r for r in self._data["reminders"].values()
                if not r.get("done") and r.get("trigger_at", 0) <= now_ts]

    def mark_reminder_fired(self, rid: str):
        r = self._data["reminders"].get(rid)
        if r:
            r["done"] = True
            r["fired_at"] = time.time()
            self._save()

    def delete_reminder(self, rid: str) -> bool:
        if rid in self._data["reminders"]:
            del self._data["reminders"][rid]
            self._save()
            return True
        return False

    # ---- 通知（收件箱）----

    def add_notification(self, ntype: str, title: str, body: str = "", cid: str = "") -> str:
        """新增一条通知。ntype: task / reminder。cid 用于点击跳转到对应会话。"""
        nid = uuid.uuid4().hex[:10]
        self._data["notifications"].append({
            "id": nid,
            "type": ntype,
            "title": title,
            "body": body,
            "cid": cid,
            "ts": time.time(),
            "read": False,
        })
        # 收件箱最多保留最近 200 条，防膨胀
        if len(self._data["notifications"]) > 200:
            self._data["notifications"] = self._data["notifications"][-200:]
        self._save()
        return nid

    def list_notifications(self):
        return sorted(self._data["notifications"], key=lambda n: n.get("ts", 0), reverse=True)

    def unread_count(self) -> int:
        return sum(1 for n in self._data["notifications"] if not n.get("read"))

    def mark_all_read(self):
        for n in self._data["notifications"]:
            n["read"] = True
        self._save()


_default_store = None


def get_default_store() -> SessionStore:
    """进程内共享的单例存储。

    main.py 的提醒工具与 web.py 共用同一个实例，避免两个实例
    各自整文件回写导致互相覆盖数据。
    """
    global _default_store
    if _default_store is None:
        _default_store = SessionStore()
    return _default_store
