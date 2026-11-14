# 本地运行说明

这是一个**从零手写的 ReAct Agent**，本地即可运行。核心只有三样东西：

1. **LLM**（大脑）——默认接 OpenAI 兼容接口，也可换 DeepSeek / 本地 Ollama
2. **Tools**（手脚）——天气、日期、限行、空气质量、汇率、计算等查询函数
3. **ReAct 循环**（流程）——把前两者串起来

## 一、配置 API Key

复制 `.env.example` 为 `.env`，填入你的 LLM 配置。任选其一：

**方式 A：阿里云千问 DashScope（当前默认，国内直连）**
```
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.8-max
```

**方式 B：DeepSeek（国内直连）**
```
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

**方式 C：OpenAI**
```
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

**方式 D：本地 Ollama（完全离线，需先装 Ollama 并 `ollama run qwen2.5`）**
```
LLM_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5
```

## 二、运行

**方式一：命令行交互**
```bash
py main.py
```

**方式二：Web UI 界面（推荐）**
```bash
py web.py
```
然后浏览器打开 http://localhost:8000 ，即可在网页里对话。

进入交互式对话，输入问题即可，例如：
- 今天深圳天气怎么样
- 今天几号，星期几
- 北京空气质量怎么样
- 100美元等于多少人民币
- 帮我算 (128+56)*7/12
- 3公里等于多少英尺
- 深圳今天限行吗

输入 `exit` 或 `quit` 退出。

## 三、零依赖说明

本 Agent 核心只用 Python 标准库。流式输出、取消请求等进阶能力需要 `requests` 库，安装：

```bash
py -m pip install requests
```

安装后自动启用流式输出（SSE）。未安装时回退到 urllib，但流式/取消能力会降级。

## 四、运行原理（ReAct 循环）

```
用户输入 → 发给 LLM（附工具清单）
         → LLM 返回：要么"要调工具"，要么"最终答案"
         → 要调工具 → 本地执行工具函数 → 结果塞回对话 → 再发给 LLM
         → 循环，直到 LLM 给出最终答案
```

工具是**本地 Python 函数**，不是让 LLM 瞎编——LLM 只负责「决定调哪个工具、传什么参数」，
真正的数据查询在本地执行，结果再交给 LLM 组织成自然语言。

## 五、LLM 调用的五大能力（stream / abort / error / retry / limit）

`llm.py` 已封装生产级的 LLM 客户端 `LLMClient`，详见 `LLM_能力说明.md`。

| 能力 | 说明 |
| --- | --- |
| stream | 流式输出，模拟打字（`chat(stream=True)` 返回生成器） |
| abort | 随时取消请求（`client.abort()`，抛 `LLMAbortedError`） |
| error | 错误分类：可重试（429/5xx/网络）vs 不可重试（401/400/超长） |
| retry | 可重试错误自动重试 3 次，指数退避 1/2/4 秒 |
| limit | 令牌桶限流（QPS）+ 上下文 token 长度检查 |

示例：

```python
from llm import LLMClient

client = LLMClient(max_retries=3, qps=2.0, max_tokens=8000)

# 流式输出
for chunk in client.chat(messages, stream=True):
    print(chunk, end="", flush=True)

# 取消
client.abort()
```

## 六、记忆功能（两层）

Agent 内置两层记忆：

| 记忆 | 说明 | 存储 |
| --- | --- | --- |
| 短期记忆 | 会话内的对话历史，能接上「那明天呢」这类指代 | 内存（进程内） |
| 长期记忆 | 用户偏好/个人信息（城市、车牌、称呼、通勤、忌口、生日……），跨会话保留 | `memory.json`（磁盘） |

**长期记忆支持任意键**（不是固定白名单）：
- **预设键**（13 个，带中文标签，注入 prompt 时显示标签）：常居城市 city、车牌尾号 car_plate、称呼 nickname、居住区域 home_area、工作城市 work_city、通勤方式 commute、饮食偏好 diet、过敏/忌口 allergy、生日 birthday、作息习惯 schedule、兴趣爱好 hobby、家庭成员 family、宠物 pet
- **自定义键**：任何你想要的键（如「咖啡口味」「车险到期」），以原文存储
- **中文标签自动归一化**：说「常居城市」会落成 `city`，避免同义重复

交互方式（全自动，由 LLM 调工具）：
- 「我常住深圳」→ `save_memory`，之后问天气不写城市也默认深圳
- 「我平时坐地铁上班」「我不吃香菜」→ 自动存 `commute` / `allergy`
- 「忘掉我的车牌」→ `forget_memory` 删除
- 命令行输入「记忆」查看全部

护栏：单条内容 ≤ 200 字、键名 ≤ 30 字、最多 50 条；注入 prompt 时最多展示最近 25 条（防 prompt 膨胀）。
记忆更新后会自动重建 system prompt，本轮之后立即生效。

代码入口：`main.py` 的 `Agent`（`_save_memory` / `_forget_memory` / `remember` / `recall`），`memory.py` 的 `Memory`（`set` / `get` / `delete` / `count` / `to_prompt` / `normalize_key`）。

## 六点五、上下文管理（四层压缩）

`context.py` 的 `ContextManager` 负责把「无限增长的对话」压回可控范围，每轮调用前自动执行。四层从主动到保底：

| 层 | 做什么 | 触发条件 |
| --- | --- | --- |
| ① 工具输入卸载 | 超大 tool 结果落盘，只留文件路径，Agent 用 `read_file` 工具按需读取 | 结果超过 `tool_dump_threshold`（默认 2000 字符） |
| ② tool message 简化 | 历史 tool 结果压成一行摘要，去冗余（最新一轮保留原文） | 每轮自动 |
| ③ 会话总结 | 按固定模板让 LLM 总结早期对话核心，非自由发挥 | token 或消息条数超阈值 |
| ④ 裁剪 | 超上限强制删最旧消息，保留 system + 最新 | 消息条数超 `max_messages` |

关键设计：
- **落盘按大小阈值触发**，小结果仍直接内联，不无脑落盘
- **总结用固定 JSON 模板**（topic / user_goal / key_facts / pending / user_prefs），防止 AI 丢关键信息
- **四层可独立开关、调阈值**（`ContextManager` 的构造参数）
- **裁剪是最后兜底**，纯本地逻辑，不依赖外部 LLM 调用

代码入口：`context.py` 的 `ContextManager`（`manage` / `maybe_dump_tool_result` / `compact_tool_messages` / `summarize` / `trim` / `read_file`）。

## 七、工具清单（20 个）

| 工具 | 功能 | 数据来源 |
| --- | --- | --- |
| get_weather | 天气（温度/体感/湿度/风力） | wttr.in 免费接口 |
| get_date_info | 日期/星期/节假日 | 本地 datetime |
| get_traffic | 机动车限行 | 本地规则表 |
| get_air_quality | 空气质量（AQI/PM2.5/PM10） | Open-Meteo 免费接口 |
| get_clothing | 穿衣建议（结合温度+天气） | 本地规则 |
| get_exchange_rate | 汇率 | frankfurter.app 免费接口 |
| get_holiday_countdown | 节假日倒计时 | 本地计算 |
| convert_unit | 单位换算（长度/重量/温度） | 本地计算 |
| calc | 安全计算器 | 本地计算（白名单防注入） |
| detect_emotion | 情绪识别（焦虑/愤怒/委屈/沮丧/害怕/内耗/孤独 + 危机信号） | 本地关键词 |
| save_memory | 写入/更新一条长期记忆（任意键，中文标签自动归一化） | memory.json |
| forget_memory | 删除一条长期记忆 | memory.json |
| set_reminder | 设置一次性提醒（自然语言时间：10分钟后 / 明天早上8点 / 周三下午3点） | sessions.json + 调度器 |
| list_reminders | 查看未触发的提醒 | sessions.json |
| cancel_reminder | 按 id 取消提醒 | sessions.json |
| read_file | 按需读取被卸载到磁盘的 tool 结果 | 本地文件 |
| use_skill | 加载指定技能的完整工作流（SKILL.md 正文 + 资源清单） | skills/ 目录 |
| read_skill_file | 读取技能附带资源或 knowledge/ 共享知识库文件（路径穿越防护） | skills/ 与 knowledge/ |
| web_search | 联网搜索（多引擎兜底、相关性过滤、5 分钟缓存） | Bing RSS / 360 搜索 |
| fetch_url | 读取网页正文（去脚本/样式，识别反爬验证页） | 目标网页 |

新增工具：在 `tools/` 下写函数 → 在 `main.py` 的 `TOOLS`（JSON Schema）和 `TOOL_IMPL`（函数映射）里注册即可。

## 七点二、联网兜底：本地没有的数据不让用户自己去找

内置工具/规则表只是「快路径」，不是能力边界。系统提示词第 8 条强制要求：

- 任何工具返回「未收录 / 查不到 / 查询失败」→ **立刻 web_search 检索**，再用 fetch_url 打开最权威的 1-2 个页面核实；
- 用户问的内容超出内置工具范围（政策新规、办事流程、票价、开售时间、某地规定）→ 同样走这条路；
- 回答必须注明来源（网站名 + 链接）与检索日期，多源冲突时说明分歧并优先官方来源；
- 检索要克制：同题最多 web_search 2-3 次、fetch_url 1-2 次；
- 确实查不到才如实说「暂时查不到」，禁止编造。

`web_search` 的实现细节（`tools/websearch.py`）：

| 环节 | 做法 |
| --- | --- |
| 引擎兜底 | Bing RSS（稳定、直链）→ 360 搜索（中文政策类更准，易限流）→ Bing HTML |
| 查询清洗 | 剥掉「今天/吗/什么时候」等口语填充词，避免把引擎带偏到天气/黄历 |
| 相关性过滤 | 关键词命中率打分；严档要求命中「核心词」（最长非数字词），全落空退宽松档并标注 |
| 查询收敛 | 长查询跑偏时自动收敛为首两个关键词重搜一次 |
| 缓存 | 同 query 5 分钟内存缓存，防止把引擎打到限流 |
| 反爬识别 | 识别「安全验证/验证码/403」，返回可读失败原因，让模型换来源而不是硬重试 |

实测：问「东莞今天限行吗」→ 工具序列 `use_skill → get_traffic → get_date_info → web_search ×3 → fetch_url`，
最终答出「东莞未实施常态化尾号限行，立沙岛全天限客车、部分路段限货车」，并附来源链接与检索日期。

## 七点五、第三方技能（Skills）

技能是「给模型的工作流指令包」，与工具互补：工具是可执行函数，技能是流程与约束。
实现位于 `skills.py`（SkillLoader），采用渐进式披露（参考 Claude Agent Skills）：

1. **L1 目录**：所有技能的 name + description 常驻 system prompt（`catalog_prompt()`）；
2. **L2 正文**：模型命中触发词后调用 `use_skill`，加载完整 SKILL.md；
3. **L3 资源**：大文件放技能目录里，模型按需 `read_skill_file` 读取。

- 技能发现：扫描项目根 `skills/*/SKILL.md`，解析 YAML frontmatter（手写解析器，零依赖）；
- 热重载：按 mtime 快照检测变更，新增/修改技能下个请求即生效；
- 安全：`read_skill_file` 拒绝 `..`/绝对路径，只允许技能目录与 `knowledge/` 内的文本文件；
- 豁免落盘：`use_skill` / `read_skill_file` 的结果是指令而非数据，不参与工具结果落盘；
- 开发规范与示例见 `../skills/README.md`，示例第三方技能：`../skills/weekly-report/`。

## 八、Web UI 界面

`web.py` + `index.html` 提供一个浏览器界面，参考 DeepSeek 布局（左侧边栏 + 右侧聊天区），复用同一个 `Agent` 后端，零新增依赖（标准库 http.server）。

| 文件 | 职责 |
| --- | --- |
| `web.py` | HTTP 服务：`/`（页面）、`/api/chat`（对话）、`/api/chat/stream`（SSE 流式）、`/api/chat/stop`（停止）、`/api/context`（上下文管理）、`/api/memory`（记忆）、`/api/conversations`（会话）、`/api/tasks`（计划任务） |
| `index.html` | 单文件前端：DeepSeek 风格极简布局（居中问候语 + 圆角输入框 + 日期分组历史 + 侧边栏底部记忆入口）、**真流式打字**、**停止按钮**、**上下文状态 + 压缩/清空**、工具轨迹折叠、记忆面板 |
| `session_store.py` | 会话 + 计划任务的本地 JSON 持久化（sessions.json） |
| `scheduler.py` | 调度器：解析计划时间 / 提醒时间（`parse_schedule`、`parse_reminder_time`）、后台线程每 30s 检查、到点自动执行任务或触发提醒 |

界面结构（对齐 DeepSeek）：
- **空状态**：问候语「你好，我能帮什么忙吗？」与输入框作为一组垂直居中；发出第一条消息后输入框下移到底部固定
- **侧边栏**：品牌行 → 圆角「开启新对话」→ 历史会话**按时间分组**（今天 / 昨天 / 7 天内 / 30 天内 / 年-月，可折叠）→ 计划任务 → 底部「长期记忆」入口
- **顶栏**：折叠按钮 + 会话标题 + 上下文状态 + 压缩/清空（极简，无分割线）
- **消息区**：用户消息浅灰气泡右对齐、助手回答无气泡直接铺排（15px / 1.85 行高），工具调用为紧凑胶囊卡片
- **输入区**：圆角 22px 卡片，聚焦时蓝色描边光晕；底部左侧快问 chips 可横向滚动、右侧圆形发送按钮（生成中变黑色方块停止）
- **深链**：支持 `/?cid=xxx` 直接打开指定会话
- **通知**：顶栏铃铛 + 未读角标 + 下拉收件箱；计划任务自动执行完成、提醒到点时写入通知并弹右下角 toast（任务类通知可点击直接跳到结果会话，「全部已读」清零）

侧边栏功能：
- **新对话**：清空当前上下文，新建一个持久化会话
- **历史会话**：按时间分组展示、点击切换、悬停删除（每个会话有独立的对话历史）。**切换会话 / 重启服务后模型仍记得前文**——`get_agent` 会把持久化历史回灌进 `agent.messages`
- **计划任务**：新建/删除/启停/立即执行。支持的时间写法：`每天 08:00`、`每天8点半`、`每周一 9点`、`每小时`、`每 6 小时`（无法识别的写法会在列表里标橙、不自动执行）。后台调度器每 30 秒检查一次，到点自动在**任务专属会话**（标题 `[任务] xxx`，出现在历史会话列表）里执行并保存结果，同时发一条完成通知；点绿点切换启停，点 ▶ 立即执行一次
- **一句话提醒**：直接说「10 分钟后提醒我喝水」「明天早上 8 点叫我起床」，模型调用 `set_reminder` 落库，到点由调度器触发为通知 toast；「我有哪些提醒」查、要取消则先列再按 id 删

顶栏功能：
- **上下文状态指示器**：实时显示当前会话的消息条数 + 估算 token 数，绿点=正常、黄点=接近上限、红点=超限
- **压缩**：手动把早期对话总结成结构化摘要（主题/目标/关键事实/待办/偏好），释放 token
- **清空**：清空当前对话历史（长期记忆保留）
- **停止按钮**：生成中「发送」按钮变成「停止」，可随时中断

接口：
- `POST /api/chat/stream`，body `{"message": "...", "cid": "..."}` → **SSE 流式**逐条推送事件：`{"type":"tool"}`（工具调用）、`{"type":"text"}`（文本增量，真流式逐 token）、`{"type":"done"}`、`{"type":"error"}`
- `POST /api/chat/stop`，body `{"cid": "..."}` → 中断当前会话正在进行的生成
- `POST /api/context`，body `{"action":"stats"|"compress"|"clear", "cid":"..."}` → 查看统计 / 手动压缩 / 清空上下文
- `POST /api/memory`，body `{"action": "get"}` / `{"action": "set", "key": ..., "value": ...}` / `{"action": "delete", "key": ...}`；返回 `{"memory": {...}, "labels": {...}}`（labels 为键的中文标签表，超限/超长返回 400 + error）
- `POST /api/conversations`，body `{"action": "list"|"create"|"get"|"delete", "cid": ...}`
- `POST /api/tasks`，body `{"action": "list"|"create"|"run"|"delete"|"toggle", ...}`；`list`/`create` 返回里带 `schedulable` 标注时间写法是否可识别；`run` 同步执行任务并返回 `{"cid", "answer", "status"}`
- `POST /api/notifications`，body `{"action": "list"|"mark_read"}` → `{"notifications": [...], "unread": N}`（任务完成 / 提醒到点的收件箱，前端每 20s 轮询）
- `POST /api/reminders`，body `{"action": "list"|"delete", "rid": ...}`（创建走对话里的 `set_reminder` 工具）

`main.py` 的 `Agent` 新增：
- `ask_stream()` 生成器：yield 事件 dict。**每轮只用一次流式请求**，通过 `chat_stream_events` 同时解析文本增量与 `tool_calls` 增量（省一半 token 与延迟，长回答也不会撑爆非流式读超时）
- `context_stats()`：返回上下文统计（消息条数 / 估算 token / 是否超限）
- `manual_compress()`：手动触发整套压缩（强制总结）
- `clear_context()`：清空上下文（保留 system prompt + 长期记忆）
- `_repair_messages()`：中断后清理「有 tool_calls 但缺 tool 响应」的残缺历史，避免下次请求 400

### 流式与停止的关键约束

1. **SSE 必须用 `Connection: close`**：SSE 无 Content-Length、无 chunked，客户端只能靠连接关闭判断结束。若用 `keep-alive`，浏览器会永远等不到结尾，表现为「回答已输出完但按钮卡在停止」。
2. **每轮请求前必须 `reset_abort()`**：否则用户点过一次停止后，客户端会永久处于取消态，后续所有请求立即被拒。
3. **abort 要主动关闭连接**：流式读取时线程阻塞在 socket read 上，仅置位 `Event` 无法唤醒，必须 `resp.close()`（放在后台线程，避免阻塞调用方）。
4. **非流式请求也用 `stream=True` 发起**：让响应头先返回、body 后读，这样「思考中」阶段点停止也能立刻打断。
5. **流式不重试**：已 yield 的 token 无法回滚，重试会造成重复输出。
6. **`_build_system_prompt` 只换头不清历史**：记忆更新会触发它重建 system prompt，若直接重置 `self.messages` 会把对话中途的历史清空、留下断裂的 tool_calls 链（下一轮请求 400）。必须保留非 system 消息。
