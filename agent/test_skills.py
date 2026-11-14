"""技能系统单元测试：frontmatter 解析 / 发现 / 目录注入 / 加载 / 资源读取 / 路径防护 / 工具注册。

运行：py agent/test_skills.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills as skills_mod
from skills import SkillLoader, parse_frontmatter

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


SKILL_A = """---
name: demo-skill
description: 演示技能。触发词：演示、demo。
---

# 演示
## 工作流
1. 打招呼
"""

SKILL_B = """---
name: second-skill
description: 第二个技能。
---
正文
"""


def main():
    # ---------- 1. frontmatter 解析 ----------
    print("[1] frontmatter 解析")
    meta, body = parse_frontmatter(SKILL_A)
    check("解析 name", meta.get("name") == "demo-skill")
    check("解析 description", meta.get("description", "").startswith("演示技能"))
    check("正文不含 frontmatter", "name:" not in body and "# 演示" in body)
    meta2, body2 = parse_frontmatter("没有 frontmatter 的文本")
    check("无 frontmatter 返回空 meta", meta2 == {} and "没有" in body2)

    # ---------- 2. 技能发现 ----------
    print("[2] 技能发现（临时目录）")
    with tempfile.TemporaryDirectory() as td:
        write(os.path.join(td, "a", "SKILL.md"), SKILL_A)
        write(os.path.join(td, "b", "SKILL.md"), SKILL_B)
        write(os.path.join(td, "bad", "SKILL.md"), "---\nname: nodesc\n---\n缺 description")
        write(os.path.join(td, "a", "references", "ref.md"), "参考内容")
        loader = SkillLoader(skills_dir=td)
        all_skills = loader.all()
        check("发现 2 个有效技能", len(all_skills) == 2, str(list(all_skills)))
        check("无效技能被跳过", "nodesc" not in all_skills)

        # ---------- 3. 目录注入 ----------
        print("[3] 目录注入")
        catalog = loader.catalog_prompt()
        check("目录含两技能名", "demo-skill" in catalog and "second-skill" in catalog)
        check("目录含 description", "演示技能" in catalog)
        empty = SkillLoader(skills_dir=os.path.join(td, "nonexist"))
        check("空目录返回空串", empty.catalog_prompt() == "")

        # ---------- 4. 加载技能 ----------
        print("[4] use_skill 加载")
        loaded = loader.load("demo-skill")
        check("正文完整返回", "# 演示" in loaded and "工作流" in loaded)
        check("资源清单列出 references/ref.md", "references/ref.md" in loaded)
        missing = loader.load("not-exist")
        check("未知技能返回可用列表", "没有找到" in missing and "demo-skill" in missing)

        # ---------- 5. 资源读取与路径防护 ----------
        print("[5] read_skill_file")
        ok = loader.read_resource("demo-skill", "references/ref.md")
        check("读取技能资源", ok == "参考内容", ok)
        bad1 = loader.read_resource("demo-skill", "../../etc/passwd")
        check("拒绝 .. 穿越", "非法" in bad1, bad1)
        bad2 = loader.read_resource("demo-skill", "..\\..\\secret.txt")
        check("拒绝反斜杠穿越", "非法" in bad2, bad2)
        bad3 = loader.read_resource("demo-skill", "/etc/passwd")
        check("拒绝绝对路径", "非法" in bad3 or "不存在" in bad3, bad3)
        nf = loader.read_resource("demo-skill", "references/nope.md")
        check("不存在文件给提示", "不存在" in nf, nf)
        ns = loader.read_resource("not-exist", "x.md")
        check("未知技能给提示", "没有找到" in ns, ns)

        # ---------- 6. 热重载 ----------
        print("[6] mtime 热重载")
        import time
        time.sleep(1.05)  # 保证 mtime 变化（Windows 文件时间精度）
        write(os.path.join(td, "c", "SKILL.md"), SKILL_B.replace("second-skill", "third-skill"))
        check("新增技能自动发现", "third-skill" in loader.all())

    # ---------- 7. 真实 skills/ 目录接入 ----------
    print("[7] 真实 skills/ 目录")
    real = skills_mod.get_default_loader().all()
    expect = {"daily-life-weather", "daily-life-calendar", "daily-life-traffic",
              "daily-life-emotion-support", "weekly-report"}
    check("发现全部 5 个内置技能", expect <= set(real), str(set(real)))
    wr = skills_mod.get_default_loader().load("weekly-report")
    check("weekly-report 列出模板资源", "references/template.md" in wr)
    tpl = skills_mod.get_default_loader().read_resource("weekly-report", "references/template.md")
    check("读取模板资源", "简洁版" in tpl, tpl[:50])
    ka = skills_mod.get_default_loader().read_resource("daily-life-weather", "knowledge/city-alias.md")
    check("knowledge/ 共享读取", len(ka) > 10 and "非法" not in ka, ka[:50])

    # ---------- 8. Agent 工具注册与 system prompt ----------
    print("[8] Agent 集成")
    from main import Agent, TOOLS, TOOL_IMPL
    from memory import Memory
    import tempfile as tf
    mem = Memory(path=os.path.join(tf.mkdtemp(), "m.json"))
    agent = Agent(memory=mem)
    names = {t["function"]["name"] for t in TOOLS}
    check("use_skill 已注册", "use_skill" in names)
    check("read_skill_file 已注册", "read_skill_file" in names)
    check("use_skill 有实现", TOOL_IMPL.get("use_skill") is not None)
    check("read_skill_file 有实现", TOOL_IMPL.get("read_skill_file") is not None)
    check("system prompt 含技能目录", "可用技能" in agent.system_prompt
          and "weekly-report" in agent.system_prompt)
    check("system prompt 含技能规则", "use_skill" in agent.system_prompt)
    r = agent._use_skill("weekly-report")
    check("agent 调用 use_skill 正常", "周报" in r, r[:80])
    r2 = agent._read_skill_file("weekly-report", "references/template.md")
    check("agent 调用 read_skill_file 正常", "简洁版" in r2, r2[:80])

    print(f"\n===== {passed} 通过 / {failed} 失败 =====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
