"""计划任务调度器：解析时间描述，到点自动执行任务。

支持的时间写法（schedule 字段，自由文本）：
  每天 08:00 / 每天 8:00 / 每天8点 / 每天早上8点 / 每天晚上7点半
  每周一 09:00 / 每周五 18:30（周一~周日）
  每小时 / 每小时 30 分（每小时第 N 分钟）
  每 2 小时 / 每 6 小时（按间隔）
无法解析的写法（如「手动触发」「有空再说」）返回 None，任务不自动跑。

去重：daily/weekly 按「日期+时间点」记 last_run_key，同一时间点只跑一次。

另外提供 parse_reminder_time：把「10分钟后」「明天早上8点」这类
一次性提醒时间解析为 Unix 时间戳。
"""
import datetime
import re
import threading
import time

try:
    from logger import get_logger
    log = get_logger("scheduler")
except ImportError:  # 允许作为独立脚本运行
    import logging
    log = logging.getLogger("scheduler")

_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s: str):
    """「十」「十二」→ 10/12；纯中文数字简单转换，失败返回 None。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if s.startswith("十") and len(s) == 2 and s[1] in _CN_NUM:
        return 10 + _CN_NUM[s[1]]
    if s.endswith("十") and len(s) == 2 and s[0] in _CN_NUM:
        return _CN_NUM[s[0]] * 10
    if "十" in s and len(s) == 3:
        a, _, b = s
        if a in _CN_NUM and b in _CN_NUM:
            return _CN_NUM[a] * 10 + _CN_NUM[b]
    return None


def _extract_hm(s: str):
    """从文本里提取 时:分。支持 08:30 / 8点 / 8点半 / 8点15 / 八点半。返回 (hour, minute) 或 (None, None)。"""
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 「点」后面的分钟部分可选（8点=8:00）
    m = re.search(r"([零一二两三四五六七八九十\d]{1,3})\s*点\s*(半|([零一二两三四五六七八九十\d]{1,3})\s*分?)?", s)
    if not m:
        return None, None
    hour = _cn_to_int(m.group(1))
    if hour is None:
        return None, None
    if m.group(2) == "半":
        minute = 30
    elif m.group(3):
        minute = _cn_to_int(m.group(3))
        if minute is None:
            return None, None
    else:
        minute = 0
    return hour, minute


def parse_reminder_time(text: str, now: datetime.datetime = None):
    """把一次性提醒的时间描述解析成 Unix 时间戳，无法解析返回 None。

    支持：「10分钟后」「半小时后」「明天早上8点」「后天下午3点半」
    「今晚 19:30」「周三上午9点」「11月20日 8点」「2026-11-20 09:00」「18:00」
    """
    if not text:
        return None
    now = now or datetime.datetime.now()
    s = text.strip()

    # 「半分钟/半小时后」等带「半」的相对时间
    m = re.search(r"半\s*(分钟|小时|天)\s*后", s)
    if m:
        unit = m.group(1)
        delta = {"分钟": 30, "小时": 1800, "天": 43200}[unit]
        return (now + datetime.timedelta(seconds=delta)).timestamp()

    # 相对时间：N 秒/分钟/小时/天 后（支持中文数字）
    m = re.search(r"([零一二两三四五六七八九十\d]{1,3})\s*(秒|分钟|小时|天|日)\s*后", s)
    if m:
        n = _cn_to_int(m.group(1))
        if n is not None:
            unit = m.group(2)
            delta = {"秒": n, "分钟": n * 60, "小时": n * 3600,
                     "天": n * 86400, "日": n * 86400}[unit]
            return (now + datetime.timedelta(seconds=delta)).timestamp()

    # 绝对日期时间：2026-11-20 09:00 / 2026年11月20日8点
    m = re.search(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?", s)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute = _extract_hm(s)
            if hour is None:
                hour, minute = 9, 0  # 只给日期默认上午 9 点
            if any(w in s for w in ("下午", "晚上", "傍晚")) and hour < 12:
                hour += 12
            target = datetime.datetime(year, month, day, hour, minute)
            return target.timestamp()
        except ValueError:
            return None

    # X月X日（今年，已过则明年）
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", s)
    if m:
        try:
            month, day = int(m.group(1)), int(m.group(2))
            hour, minute = _extract_hm(s)
            if hour is None:
                hour, minute = 9, 0
            if any(w in s for w in ("下午", "晚上", "傍晚")) and hour < 12:
                hour += 12
            target = datetime.datetime(now.year, month, day, hour, minute)
            if target <= now:
                target = datetime.datetime(now.year + 1, month, day, hour, minute)
            return target.timestamp()
        except ValueError:
            return None

    # 日期偏移：今天 / 明天 / 后天 / 大后天
    day_offset = 0
    if "大后天" in s:
        day_offset = 3
    elif "后天" in s:
        day_offset = 2
    elif "明天" in s:
        day_offset = 1

    hour, minute = _extract_hm(s)

    # 只给时段不给具体点：明天早上→8点、今晚→20点
    if hour is None:
        defaults = {"早上": 8, "早晨": 8, "上午": 9, "中午": 12,
                    "下午": 15, "傍晚": 18, "晚上": 20, "今晚": 20, "凌晨": 5}
        for k, v in defaults.items():
            if k in s:
                hour, minute = v, 0
                break
        if hour is None and day_offset > 0:
            hour, minute = 9, 0  # 「明天提醒我」默认上午 9 点
    if hour is None:
        return None

    # 下午/晚上/傍晚/今晚 且小时 < 12 → +12
    if any(w in s for w in ("下午", "晚上", "傍晚", "今晚")) and hour < 12:
        hour += 12
    if "中午" in s and 1 <= hour <= 5:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    target = (now + datetime.timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)

    # 星期X：取下一次该星期
    mw = re.search(r"(?:周|星期)([一二三四五六日天])", s)
    if mw and day_offset == 0:
        wd = _WEEKDAYS[mw.group(1)]
        days_ahead = (wd - now.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        target = target + datetime.timedelta(days=days_ahead)
    elif day_offset == 0 and "今天" not in s and "今晚" not in s and target <= now:
        # 今天的这个时间已过 → 顺延到明天
        target += datetime.timedelta(days=1)

    return target.timestamp()


def parse_schedule(text: str):
    """把人类可读的时间描述解析成可执行计划，无法解析返回 None。"""
    if not text:
        return None
    s = text.strip()
    if not s or s in ("手动触发", "手动", "无"):
        return None

    # 每 N 小时（如「每 6 小时」）
    m = re.search(r"每\s*(\d{1,2})\s*个?小时", s)
    if m:
        hours = int(m.group(1))
        if hours >= 2:
            return {"kind": "interval", "seconds": hours * 3600}

    # 每小时（可带「第 N 分钟」）
    if "每小时" in s:
        m = re.search(r"(\d{1,2})\s*分", s)
        minute = int(m.group(1)) if m else 0
        if 0 <= minute <= 59:
            return {"kind": "hourly", "minute": minute}
        return None

    # 每周X / 星期X（可选，缺省为每天）
    weekday = None
    for name, idx in _WEEKDAYS.items():
        if f"周{name}" in s or f"星期{name}" in s:
            weekday = idx
            break

    # 每日时间点：08:00 / 8:00 / 8点 / 8点半 / 8点15 / 晚上7点半
    hm = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", s)
    if hm:
        hour, minute = int(hm.group(1)), int(hm.group(2))
    else:
        dm = re.search(r"(\d{1,2})\s*点\s*(半|(\d{1,2})\s*分?)?", s)
        if not dm:
            return None
        hour = int(dm.group(1))
        if dm.group(2) == "半":
            minute = 30
        elif dm.group(3):
            minute = int(dm.group(3))
        else:
            minute = 0

    # 下午/晚上/傍晚 且小时 < 12 → +12
    if any(w in s for w in ("下午", "晚上", "傍晚", "今晚")) and hour < 12:
        hour += 12
    # 「中午 1 点」= 13 点
    if "中午" in s and 1 <= hour <= 5:
        hour += 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    sch = {"kind": "daily", "hour": hour, "minute": minute}
    if weekday is not None:
        sch["kind"] = "weekly"
        sch["weekday"] = weekday
    return sch


def is_due(sch: dict, task: dict, now=None) -> bool:
    """判断任务在当前时刻是否到期（且本周期内还没跑过）。"""
    now = now or time.localtime()
    if sch["kind"] == "interval":
        last = task.get("last_run", 0)
        return (time.time() - last) >= sch["seconds"]
    if sch["kind"] == "hourly":
        if now.tm_min != sch["minute"]:
            return False
        key = f"{now.tm_year}-{now.tm_yday}-{now.tm_hour}"
        return task.get("last_run_key") != key
    if sch["kind"] == "daily":
        if (now.tm_hour, now.tm_min) != (sch["hour"], sch["minute"]):
            return False
        key = f"{now.tm_year}-{now.tm_yday}"
        return task.get("last_run_key") != key
    if sch["kind"] == "weekly":
        if now.tm_wday != sch["weekday"]:
            return False
        if (now.tm_hour, now.tm_min) != (sch["hour"], sch["minute"]):
            return False
        key = f"{now.tm_year}-{now.tm_yday}"
        return task.get("last_run_key") != key
    return False


def run_key(sch: dict, now=None) -> str:
    now = now or time.localtime()
    if sch["kind"] == "hourly":
        return f"{now.tm_year}-{now.tm_yday}-{now.tm_hour}"
    return f"{now.tm_year}-{now.tm_yday}"


class TaskScheduler:
    """后台线程：周期检查计划任务 + 一次性提醒，到点的交给对应函数执行。"""

    def __init__(self, store, run_fn, reminder_fn=None, interval: int = 30,
                 cleanup_fn=None, cleanup_every: int = 86400):
        self.store = store
        self.run_fn = run_fn            # run_fn(task_dict)
        self.reminder_fn = reminder_fn  # reminder_fn(reminder_dict)，可选
        self.interval = interval        # 检查周期（秒）
        self.cleanup_fn = cleanup_fn    # 定期清理函数（如清理落盘的工具结果），可选
        self.cleanup_every = cleanup_every  # 清理周期（秒，默认每天）
        self._last_cleanup = 0.0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="task-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._maybe_cleanup()
                self.tick()
            except Exception:
                log.exception("调度器单轮检查异常（下轮继续）")
            self._stop.wait(self.interval)

    def _maybe_cleanup(self):
        """到清理周期就执行一次清理（默认每天），失败不影响调度。"""
        if self.cleanup_fn is None:
            return
        if time.time() - self._last_cleanup < self.cleanup_every:
            return
        self._last_cleanup = time.time()
        try:
            removed = self.cleanup_fn()
            if removed:
                log.info("定期清理完成：删除 %s 个过期落盘文件", removed)
        except Exception:
            log.exception("定期清理失败")

    def tick(self):
        """检查一遍所有任务与提醒，执行到期的（公开出来方便测试）。"""
        for task in self.store.list_tasks():
            if not task.get("enabled", True):
                continue
            sch = parse_schedule(task.get("schedule", ""))
            if not sch or not is_due(sch, task):
                continue
            # 先占位再执行，防止执行期间下一轮 tick 重复触发
            self.store.update_task(task["id"], last_run=time.time(), last_run_key=run_key(sch))
            threading.Thread(target=self._safe_run, args=(task,), daemon=True).start()

        # 一次性提醒：到点触发，先标记再执行防重复
        if self.reminder_fn is not None:
            for rem in self.store.due_reminders(time.time()):
                self.store.mark_reminder_fired(rem["id"])
                threading.Thread(target=self._safe_remind, args=(rem,), daemon=True).start()

    def _safe_run(self, task):
        log.info("执行计划任务：%s（%s）", task.get("name"), task.get("schedule"))
        try:
            self.run_fn(task)
            log.info("计划任务完成：%s", task.get("name"))
        except Exception as e:
            log.exception("计划任务失败：%s", task.get("name"))
            try:
                self.store.update_task(task["id"], last_status=f"error: {e}")
            except Exception:
                log.exception("写入任务失败状态时出错")

    def _safe_remind(self, rem):
        log.info("触发提醒：%s", rem.get("message"))
        try:
            self.reminder_fn(rem)
        except Exception:
            log.exception("触发提醒失败：%s", rem.get("id"))
