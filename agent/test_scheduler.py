"""调度器 / 提醒解析 / 存储上限 / 落盘清理 / 节假日数据的单元测试。

覆盖此前只在会话里临时跑过、未入库的用例，防止回归。
运行：py -X utf8 test_scheduler.py
"""
import datetime
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scheduler as sch_mod
from scheduler import parse_schedule, parse_reminder_time, is_due, run_key
from session_store import SessionStore, MAX_MESSAGES_PER_CONVERSATION
from context import cleanup_dumps
from tools.calendar_tool import load_holidays, get_date_info
from tools.local_utils import get_holiday_countdown

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def ts(y, mo, d, h=0, mi=0):
    """构造某个本地时刻的 struct_time，供 is_due 使用。"""
    return time.localtime(datetime.datetime(y, mo, d, h, mi).timestamp())


# ---------- ① parse_schedule ----------
print("\n[1] parse_schedule 计划表达式")
cases = [
    ("每天 08:00", {"kind": "daily", "hour": 8, "minute": 0}),
    ("每天8点", {"kind": "daily", "hour": 8, "minute": 0}),
    ("每天早上8点", {"kind": "daily", "hour": 8, "minute": 0}),
    ("每天晚上7点半", {"kind": "daily", "hour": 19, "minute": 30}),
    ("每天 18:30", {"kind": "daily", "hour": 18, "minute": 30}),
    ("每周一 09:00", {"kind": "weekly", "weekday": 0, "hour": 9, "minute": 0}),
    ("每周五下午6点", {"kind": "weekly", "weekday": 4, "hour": 18, "minute": 0}),
    ("每小时", {"kind": "hourly", "minute": 0}),
    ("每小时 30 分", {"kind": "hourly", "minute": 30}),
    ("每 2 小时", {"kind": "interval", "seconds": 7200}),
    ("每 6 小时", {"kind": "interval", "seconds": 21600}),
]
for text, expect in cases:
    got = parse_schedule(text)
    check(f"解析「{text}」", got == expect, f"got={got} expect={expect}")

for text in ("手动触发", "手动", "无", "", "随便", "有空再说"):
    check(f"不可解析「{text}」返回 None", parse_schedule(text) is None, parse_schedule(text))

# ---------- ② parse_reminder_time ----------
print("\n[2] parse_reminder_time 一次性提醒时间")
NOW = datetime.datetime(2026, 11, 16, 10, 0)  # 周一 10:00

r = parse_reminder_time("10分钟后", NOW)
check("10分钟后 = +600s", r is not None and abs(r - (NOW.timestamp() + 600)) < 1)

r = parse_reminder_time("半小时后", NOW)
check("半小时后 = +1800s", r is not None and abs(r - (NOW.timestamp() + 1800)) < 1)

r = parse_reminder_time("2小时后", NOW)
check("2小时后 = +7200s", r is not None and abs(r - (NOW.timestamp() + 7200)) < 1)

r = parse_reminder_time("明天早上8点", NOW)
check("明天早上8点 = 11-17 08:00",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 17, 8, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("后天下午3点半", NOW)
check("后天下午3点半 = 11-18 15:30",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 18, 15, 30),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("今晚19:30", NOW)
check("今晚19:30 = 当天 19:30",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 16, 19, 30),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("周三上午9点", NOW)
check("周三上午9点 = 11-18 09:00（下次周三）",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 18, 9, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("11月20日 8点", NOW)
check("11月20日8点 = 11-20 08:00",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 20, 8, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("2026-12-01 09:00", NOW)
check("绝对日期 2026-12-01 09:00",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 12, 1, 9, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("18:00", NOW)
check("18:00（今天已定，未过）→ 当天 18:00",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 16, 18, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

r = parse_reminder_time("9:00", NOW)
check("9:00（今天已过）→ 顺延明天 09:00",
      r is not None and datetime.datetime.fromtimestamp(r) == datetime.datetime(2026, 11, 17, 9, 0),
      datetime.datetime.fromtimestamp(r) if r else None)

check("空字符串返回 None", parse_reminder_time("", NOW) is None)
check("纯文本返回 None", parse_reminder_time("等我有空", NOW) is None)

# ---------- ③ is_due / run_key ----------
print("\n[3] is_due 去重判定")
daily = {"kind": "daily", "hour": 8, "minute": 0}
check("daily 到点且未跑 → True", is_due(daily, {}, ts(2026, 11, 16, 8, 0)) is True)
check("daily 已跑过本日 → False",
      is_due(daily, {"last_run_key": "2026-320"}, ts(2026, 11, 16, 8, 0)) is False,
      ts(2026, 11, 16, 8, 0).tm_yday)
check("daily 未到点 → False", is_due(daily, {}, ts(2026, 11, 16, 8, 1)) is False)

weekly = {"kind": "weekly", "weekday": 0, "hour": 9, "minute": 0}  # 周一
check("weekly 周一 09:00 → True", is_due(weekly, {}, ts(2026, 11, 16, 9, 0)) is True)
check("weekly 周二 → False", is_due(weekly, {}, ts(2026, 11, 17, 9, 0)) is False)

hourly = {"kind": "hourly", "minute": 30}
now_h = ts(2026, 11, 16, 14, 30)
hourly_key = run_key(hourly, now_h)
check("hourly 到点且未跑 → True", is_due(hourly, {}, now_h) is True)
check("hourly 本小时已跑 → False", is_due(hourly, {"last_run_key": hourly_key}, now_h) is False)

interval = {"kind": "interval", "seconds": 3600}
check("interval 从未跑 → True", is_due(interval, {}, ts(2026, 11, 16, 12, 0)) is True)
check("interval 刚跑过 → False",
      is_due(interval, {"last_run": time.time() - 60}, ts(2026, 11, 16, 12, 0)) is False)
check("interval 超时 → True",
      is_due(interval, {"last_run": time.time() - 7200}, ts(2026, 11, 16, 12, 0)) is True)

check("run_key daily 按天", run_key(daily, ts(2026, 11, 16, 8, 0)).startswith("2026-"))
check("run_key hourly 按小时", run_key(hourly, now_h).endswith("-14"))

# ---------- ④ 会话存储上限 ----------
print("\n[4] 会话消息存储上限")
tmpdir = tempfile.mkdtemp(prefix="agent-test-")
store = SessionStore(path=os.path.join(tmpdir, "s.json"))
cid = store.create_conversation("测试")
msgs = [{"role": "user", "content": f"m{i}"} for i in range(MAX_MESSAGES_PER_CONVERSATION + 25)]
store.update_conversation(cid, messages=msgs)
conv = store.get_conversation(cid)
check("超出上限被截断", len(conv["messages"]) == MAX_MESSAGES_PER_CONVERSATION, len(conv["messages"]))
check("保留的是最近的消息", conv["messages"][-1]["content"] == f"m{MAX_MESSAGES_PER_CONVERSATION + 24}")
check("记录累计截断数", conv.get("truncated_count") == 25, conv.get("truncated_count"))
check("持久化到磁盘", os.path.exists(os.path.join(tmpdir, "s.json")))

store2 = SessionStore(path=os.path.join(tmpdir, "s.json"))
check("重新加载后仍生效", len(store2.get_conversation(cid)["messages"]) == MAX_MESSAGES_PER_CONVERSATION)

# ---------- ⑤ 落盘清理 ----------
print("\n[5] context_cache 落盘清理")
dumpdir = os.path.join(tmpdir, "dumps")
os.makedirs(dumpdir)
old = os.path.join(dumpdir, "old.txt")
new = os.path.join(dumpdir, "new.txt")
for p in (old, new):
    open(p, "w", encoding="utf-8").write("x")
past = time.time() - 8 * 86400
os.utime(old, (past, past))
removed = cleanup_dumps(dumpdir, max_age_days=7)
check("过期文件被删除", removed == 1 and not os.path.exists(old), removed)
check("未过期文件保留", os.path.exists(new))
check("目录不存在时返回 0", cleanup_dumps(os.path.join(tmpdir, "nope")) == 0)

# ---------- ⑥ 节假日数据 ----------
print("\n[6] 节假日数据与跨年兜底")
data = load_holidays()
check("收录 2026", "2026" in data and "春节" in data["2026"])
check("收录 2027", "2027" in data and "春节" in data["2027"])
check("2026 春节日期正确", data["2026"]["春节"]["date"] == "2026-02-17")
check("2027 春节日期正确", data["2027"]["春节"]["date"] == "2027-02-06")

info_2026 = get_date_info("2026-10-03")
check("2026-10-03 命中国庆假期", "国庆" in info_2026, info_2026)
info_2027 = get_date_info("2027-02-08")
check("2027-02-08 命中春节假期", "春节" in info_2027, info_2027)
info_far = get_date_info("2030-01-01")
check("2030 未收录 → 给联网指令", "web_search" in info_far, info_far)
check("2030 未收录 → 不含虚构假期", "假期" not in info_far, info_far)

cd_far = get_holiday_countdown("春节", 2030)
check("倒计时未收录年份 → 给联网指令", "web_search" in cd_far, cd_far)
cd_2027 = get_holiday_countdown("春节", 2027)
check("倒计时 2027 春节可算", "2027" in cd_2027 and "天" in cd_2027, cd_2027)

# ---------- 汇总 ----------
print(f"\n{'=' * 40}\n通过 {passed} 项，失败 {failed} 项")
sys.exit(0 if failed == 0 else 1)
