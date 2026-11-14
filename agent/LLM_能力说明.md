# LLM 调用封装 —— 五大能力说明

`llm.py` 已重构为生产级封装，围绕一次 LLM 调用提供五大能力。本文说明每个能力怎么用、怎么验证。

## 核心类

```python
from llm import LLMClient

client = LLMClient(
    api_key="",          # 留空则读 .env
    base_url="",         # 留空则读 .env
    model="",            # 留空则读 .env
    max_retries=3,       # 重试次数
    timeout=60.0,        # 单次请求超时（秒）
    qps=2.0,             # 限流：每秒最大请求数
    max_tokens=0,        # 上下文 token 上限，0=不限制
)
```

---

## ① stream —— 流式输出（模拟打字）

```python
for chunk in client.chat(messages, stream=True):
    print(chunk, end="", flush=True)  # 边收边打印，模拟打字
```

- `stream=True` 返回**生成器**，逐段 yield 文本增量。
- `stream=False`（默认）返回完整 message dict。
- 依赖 `requests` 库（已安装）。

## ② abort —— 取消请求

```python
client.abort()  # 随时中断当前/后续请求
```

- 基于 `threading.Event`，跨线程可中断。
- 取消后抛出 `LLMAbortedError`。
- 新请求前调用 `client.reset_abort()` 清空取消标记。
- 典型用法：用户在流式输出中点了「停止」按钮。

## ③ error —— 错误分类

所有异常继承 `LLMError`，子类明确区分：

| 异常 | 触发 | 是否重试 |
|---|---|---|
| `LLMRateLimitError` | 429 限流 | 是 |
| `LLMServerError` | 5xx 服务端错误 | 是 |
| `LLMNetworkError` | 网络超时/连接失败/非JSON响应 | 是 |
| `LLMAuthError` | 401/403 鉴权失败 | **否** |
| `LLMBadRequestError` | 400 参数错误 | **否** |
| `LLMContextTooLongError` | 上下文超长 | **否** |
| `LLMAbortedError` | 用户主动取消 | **否** |

捕获时按需处理：

```python
try:
    msg = client.chat(messages)
except LLMAuthError:
    print("Key 有问题，请检查配置")   # 重试没用
except LLMRateLimitError:
    print("请求太频繁，稍后再试")
except LLMError:
    print("其他错误")
```

## ④ retry —— 自动重试

- 只对**可重试错误**（429/5xx/网络）自动重试。
- 重试次数由 `max_retries` 控制（默认 3）。
- 采用**指数退避**：第 1/2/3 次重试前等待 1/2/4 秒。
- 不可重试错误（鉴权/参数/超长）直接抛出，不浪费时间。

## ⑤ limit —— 限制

### 限流（QPS）
内置 `TokenBucket` 令牌桶，控制每秒最大请求数（`qps` 参数）。超过频率时自动等待，避免触发服务商的 429。

### 上下文长度
`max_tokens` 参数（0=不限制）。发请求前估算 token 数，超长直接抛 `LLMContextTooLongError`，避免把请求发出去才被拒。

---

## 便捷函数（兼容旧代码）

```python
from llm import chat, chat_stream, abort

msg = chat(messages)              # 非流式
for c in chat_stream(messages):   # 流式
    ...
abort()                           # 取消默认客户端
```

---

## 验证记录（2026-11-11）

| 能力 | 验证方式 | 结果 |
|---|---|---|
| 基础连通 | 非流式问「收到」 | ✅ 返回「收到」 |
| stream | 流式打印自我介绍 | ✅ 边收边打印 |
| abort | 收到 23 字符后 cancel | ✅ 立即中断，抛 LLMAbortedError |
| error 分类 | 错误 Key 触发 401 | ✅ 归为 LLMAuthError，不重试 |
| retry | 指向无效端口 | ✅ 重试 3 次 + 指数退避 |
| limit-QPS | 令牌桶取 5 个令牌 | ✅ 1.5s 精确节流 |
| limit-context | max_tokens=100 + 长消息 | ✅ 触发 LLMContextTooLongError |
| 回归 | ReAct 天气查询 | ✅ 正常返回并给建议 |
