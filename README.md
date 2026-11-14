# 日常生活智能体（Daily Life Agent）

一个本地可运行的 ReAct Agent（LLM + Tools + ReAct 循环），带 Web UI、长期记忆、
计划任务调度、提醒通知，并支持**第三方技能（Skills）即插即用**。

## 能做什么

| 能力 | 触发示例 | 实现 |
| --- | --- | --- |
| 天气查询 | 「今天深圳天气怎么样」 | 内置工具 `get_weather` |
| 日历/节假日/倒计时 | 「下个节假日是哪天」 | `get_date_info` / `get_holiday_countdown` |
| 限行/出行 | 「今天深圳限行吗」「东莞限行吗」 | `get_traffic` + 未收录自动联网 |
| 空气质量 / 汇率 | 「深圳空气怎么样」「美元兑人民币」 | `get_air_quality` / `get_exchange_rate` |
| 联网自查 | 内置工具查不到时的兜底：政策新规、办事流程、票价等 | `web_search` / `fetch_url` |
| 穿衣建议 / 单位换算 / 计算 | 「15度穿什么」「100 千米是多少米」 | `get_clothing` / `convert_unit` / `calc` |
| 情绪支持 | 「最近压力好大」 | `detect_emotion` + 情绪支持原则 |
| 长期记忆 | 「我常住深圳」「忘掉我的车牌」 | `save_memory` / `forget_memory` + Web 面板 |
| 一次性提醒 | 「10分钟后提醒我喝水」 | `set_reminder` / `list_reminders` / `cancel_reminder` |
| 计划任务 | 在 Web 面板创建定时任务，到点自动跑 | `scheduler.py` |
| **第三方技能** | 「帮我写周报」…… | `use_skill` / `read_skill_file`，见下 |

## 第三方技能（Skills）

技能是给模型的「工作流指令包」，放在 `skills/` 下即被自动发现，**无需改代码**：

```
skills/
├── README.md                    # 技能开发指南（写技能前必读）
├── weather/SKILL.md             # 天气查询工作流
├── calendar/SKILL.md            # 日历/节假日工作流
├── traffic/SKILL.md             # 限行查询工作流
├── emotion-support/SKILL.md     # 情绪调节工作流
└── weekly-report/               # 示例第三方技能
    ├── SKILL.md                 #   周报生成工作流
    └── references/template.md   #   附带资源（演示三级加载）
```

工作原理是**渐进式披露**（参考 Claude Agent Skills）：

1. **L1 目录**：只有每个技能的 name + description 常驻 system prompt；
2. **L2 正文**：模型命中触发词后调用 `use_skill`，才加载完整 SKILL.md；
3. **L3 资源**：大段参考资料放技能目录里，模型按需 `read_skill_file` 读取。

新增技能 = 新建 `skills/<名字>/SKILL.md`（frontmatter 写好触发词），保存即生效。
格式规范、写作技巧与安全约定见 `skills/README.md`。

## 查不到就自己去找（联网兜底）

内置工具和规则表只是快路径。任何工具返回「未收录 / 查不到 / 查询失败」，
或用户问的超出内置范围（某地新规、办事流程、票价、开售时间等），
Agent 会自动 `web_search` 检索、`fetch_url` 打开权威页面核实，
然后在回答里注明来源链接与检索日期；多来源冲突会说明分歧，全部查不到才如实说明。

例：问「东莞限行吗」——本地规则表只收录了北京/深圳/成都，
现在它会去查东莞交警相关信息，答「东莞未实施常态化尾号限行，立沙岛全天限客车、部分路段限货车」，并附来源。

## 目录结构

```
daily-life-agent-starter/
├── README.md               # 本文件
├── skills/                 # 第三方技能目录（即插即用）
├── knowledge/
│   └── city-alias.md       # 城市别名共享知识库
└── agent/                  # 本地可运行的 ReAct Agent（Python）
    ├── web.py              # Web 服务（DeepSeek 风格 UI，SSE 真流式）
    ├── main.py             # Agent 核心：ReAct 循环 + 工具注册
    ├── skills.py           # 技能加载器（扫描/解析/热重载/路径防护）
    ├── llm.py              # LLM 调用封装（OpenAI 兼容，流式 + 中断）
    ├── memory.py           # 长期记忆（memory.json 持久化）
    ├── context.py          # 上下文四层压缩
    ├── scheduler.py        # 计划任务/提醒调度器
    ├── session_store.py    # 会话/任务/提醒/通知持久化
    ├── index.html          # Web UI
    ├── test_skills.py      # 技能系统单测（31 项）
    ├── test_websearch.py   # 联网检索单测（31 项）
    └── tools/              # 工具实现（本地真实函数）
        ├── websearch.py    #   联网搜索 + 网页正文抓取
        └── traffic.py      #   限行规则表（未收录时引导联网）
```

## 运行

```bash
# 配置 API Key：复制 agent/.env.example 为 agent/.env 填入 LLM_API_KEY
py agent/web.py        # Web UI（推荐）：http://localhost:8000
py agent/main.py       # 命令行版
```

零三方依赖：Python 标准库即可运行（LLM 走 OpenAI 兼容接口）。
