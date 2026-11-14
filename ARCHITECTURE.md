# 架构说明与扩展指南

## 三层架构

```
用户输入
   ↓
① 意图识别层  —— 主 Skill 根据触发词路由到具体子 Skill
   ↓
② 工具能力层  —— 多个独立 Skill：weather / calendar / traffic
   ↓
③ 数据源层    —— 内置联网搜索 / 知识库（后续可接真实 API）
```

## 设计原则

1. **单一职责**：每个 Skill 只负责一类查询，互不耦合。
2. **可插拔**：新增能力 = 新增一个 Skill 目录，不改动已有 Skill。
3. **触发词精确**：每个 Skill 的 `description` 里写清触发词与反触发（负面边界），避免误召回。
4. **纯查询边界**：第一版只回答查询，不做写操作、不做提醒调度。

## 新增一个 Skill 的标准步骤

1. 在 `skills/` 下新建目录，如 `skills/food/`。
2. 编写 `SKILL.md`，包含：
   - YAML frontmatter：`name`、`description`（含触发词 + 适用边界 + 反触发词）。
   - 明确的 Workflow（步骤 SOP）。
   - 约束规则（Constraints）。
   - 1-2 个 Few-Shot 示例。
3. 如需共享知识，写入 `knowledge/` 目录并在 SKILL.md 中引用。
4. 跑四道关自检：叫得应 / 做得对 / 划算耐用 / 小白容错。

## SKILL.md Frontmatter 模板

```yaml
---
name: skill-name
description: 一句话说明。触发词：xxx、xxx。适用场景：xxx。不适用于：xxx。
---
```

> `description` 是召回（叫得应）的关键，务必包含：正触发词 + 边界 + 负触发词。
