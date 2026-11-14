"""第三方技能（Skill）加载器：让 Agent 支持即插即用的技能扩展。

设计参考 Claude Agent Skills 的「渐进式披露」（progressive disclosure）：

  Level 1 —— 目录：只把每个技能的 name + description 注入 system prompt，
             让模型知道「有哪些技能、什么时候该用」，代价极小（每技能约几十 token）。
  Level 2 —— 正文：模型判定命中后调用 use_skill 工具，才把完整的 SKILL.md
             工作流加载进上下文。
  Level 3 —— 资源：技能目录下的附加文件（references/、templates/ 等）不进上下文，
             模型按需用 read_skill_file 工具读取。

技能格式（与 Claude Skills 兼容）：
  skills/<技能目录>/SKILL.md
    ---
    name: 技能名（唯一，建议与目录同名）
    description: 一段话说清「做什么 + 触发词 + 适用/不适用场景」
    ---
    （正文：目标 / 输入约定 / 工作流 SOP / 约束 / Few-Shot 示例）

安全约定：
  - 技能正文是「指令」，会被注入模型上下文 —— 只安装你信任的第三方技能；
  - read_skill_file 做了路径穿越防护，只能读取技能目录与共享 knowledge/ 目录内的文件；
  - 技能不直接执行任何代码（纯文本指令），可执行能力一律走既有工具。
"""
import os
import re
import threading

# skills/ 与 knowledge/ 都在项目根（agent/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")
KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, "knowledge")

# 单个技能正文 / 资源文件的最大读取字符数（防把超大文件塞进上下文）
MAX_SKILL_CHARS = 20000
MAX_RESOURCE_CHARS = 8000

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class Skill:
    """一个已发现的技能。"""

    __slots__ = ("name", "description", "dir", "skill_md", "mtime")

    def __init__(self, name, description, skill_dir, skill_md, mtime):
        self.name = name
        self.description = description
        self.dir = skill_dir
        self.skill_md = skill_md
        self.mtime = mtime

    def list_resources(self):
        """列出技能目录下除 SKILL.md 外的资源文件（相对路径）。"""
        out = []
        for root, _dirs, files in os.walk(self.dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.dir)
                if rel.replace("\\", "/") != "SKILL.md":
                    out.append(rel.replace("\\", "/"))
        return sorted(out)


def parse_frontmatter(text: str):
    """解析 YAML frontmatter（极简手写版，只支持 key: value 单行），返回 (meta, body)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[m.end():]


def _parse_skill_file(skill_md: str):
    """解析单个 SKILL.md，返回 Skill 或 None（缺 name/description 视为无效）。"""
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    meta, _body = parse_frontmatter(text)
    name = meta.get("name") or os.path.basename(os.path.dirname(skill_md))
    description = meta.get("description", "").strip()
    if not description:
        return None
    return Skill(
        name=name,
        description=description,
        skill_dir=os.path.dirname(skill_md),
        skill_md=skill_md,
        mtime=os.path.getmtime(skill_md),
    )


class SkillLoader:
    """扫描并缓存 skills/ 目录下的全部技能；文件变动时自动重载。"""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self.skills_dir = skills_dir
        self._skills = {}
        self._snapshot = None  # (路径, mtime) 快照，用于变更检测
        self._lock = threading.Lock()

    # ---- 发现与缓存 ----

    def _scan(self):
        skills = {}
        snapshot = []
        if os.path.isdir(self.skills_dir):
            for entry in sorted(os.listdir(self.skills_dir)):
                skill_md = os.path.join(self.skills_dir, entry, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue
                skill = _parse_skill_file(skill_md)
                if skill is None:
                    continue
                skills[skill.name] = skill
                snapshot.append((skill_md, skill.mtime))
        return skills, tuple(snapshot)

    def _maybe_reload(self):
        with self._lock:
            if self._snapshot is None:
                self._skills, self._snapshot = self._scan()
                return
            # 轻量变更检测：比对每个 SKILL.md 的 mtime 与数量
            current = []
            if os.path.isdir(self.skills_dir):
                for entry in sorted(os.listdir(self.skills_dir)):
                    p = os.path.join(self.skills_dir, entry, "SKILL.md")
                    if os.path.isfile(p):
                        current.append((p, os.path.getmtime(p)))
            if tuple(current) != self._snapshot:
                self._skills, self._snapshot = self._scan()

    # ---- 对外接口 ----

    def all(self):
        """返回 {name: Skill}，按 name 排序。"""
        self._maybe_reload()
        return dict(sorted(self._skills.items()))

    def catalog_prompt(self) -> str:
        """生成注入 system prompt 的技能目录（Level 1）。"""
        skills = self.all()
        if not skills:
            return ""
        lines = ["可用技能（Skills）："]
        for name, s in skills.items():
            lines.append(f"- {name}: {s.description}")
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """加载技能完整正文（Level 2），供 use_skill 工具返回。"""
        skills = self.all()
        skill = skills.get((name or "").strip())
        if skill is None:
            known = "、".join(skills) or "（无）"
            return f"没有找到技能「{name}」。当前可用技能：{known}"
        try:
            with open(skill.skill_md, "r", encoding="utf-8") as f:
                body = f.read(MAX_SKILL_CHARS + 1)
        except OSError as e:
            return f"读取技能失败：{e}"
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS] + "\n…（技能正文过长已截断）"
        resources = skill.list_resources()
        header = f"# 已加载技能：{skill.name}\n"
        if resources:
            header += (
                "# 该技能附带的资源文件（用 read_skill_file 按需读取，"
                "不要一次全读）：\n" + "\n".join(f"- {r}" for r in resources) + "\n"
            )
        return header + "\n" + body

    def read_resource(self, name: str, relpath: str) -> str:
        """读取技能附带的资源文件（Level 3）。

        relpath 相对于技能目录；也允许以 knowledge/ 开头读项目共享知识库。
        路径穿越（.. 等）会被拒绝。
        """
        relpath = (relpath or "").strip().replace("\\", "/").lstrip("/")
        if not relpath or relpath.split("/")[0] in ("..", ".") or "/../" in f"/{relpath}/":
            return "非法路径。"

        if relpath.startswith("knowledge/"):
            base = PROJECT_ROOT
        else:
            skills = self.all()
            skill = skills.get((name or "").strip())
            if skill is None:
                return f"没有找到技能「{name}」。"
            base = skill.dir

        path = os.path.realpath(os.path.join(base, relpath))
        allowed_root = os.path.realpath(base if not relpath.startswith("knowledge/")
                                        else KNOWLEDGE_DIR)
        if os.path.commonpath([path, allowed_root]) != allowed_root:
            return "非法路径：只能读取技能目录或 knowledge/ 内的文件。"
        if not os.path.isfile(path):
            return f"文件不存在：{relpath}"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_RESOURCE_CHARS + 1)
        except (OSError, UnicodeDecodeError) as e:
            return f"读取失败（可能不是文本文件）：{e}"
        if len(content) > MAX_RESOURCE_CHARS:
            content = content[:MAX_RESOURCE_CHARS] + "\n…（文件过长已截断）"
        return content


_default_loader = None


def get_default_loader() -> SkillLoader:
    """进程级单例：所有会话共享同一份技能缓存（mtime 变更自动重载）。"""
    global _default_loader
    if _default_loader is None:
        _default_loader = SkillLoader()
    return _default_loader
