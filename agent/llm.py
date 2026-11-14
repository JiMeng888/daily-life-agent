"""LLM 调用封装（生产级）：OpenAI 兼容接口，支持 DeepSeek / 千问 / OpenAI / Ollama。

五大能力：
  1. stream   —— 流式输出（生成器），可模拟打字效果
  2. abort    —— 通过 threading.Event 随时中断请求
  3. error    —— 错误分类（可重试 / 不可重试）
  4. retry    —— 可重试错误自动重试（指数退避）
  5. limit    —— 限流（令牌桶控制 QPS）+ 上下文长度检查

零第三方依赖：优先用 requests（若已安装），否则回退到 urllib。
"""
import json
import os
import threading
import time

try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    import urllib.request
    import urllib.error


# ============ 异常体系 ============

class LLMError(Exception):
    """LLM 调用基础异常。"""


class LLMRateLimitError(LLMError):
    """429 限流错误（可重试）。"""


class LLMServerError(LLMError):
    """5xx 服务端错误（可重试）。"""


class LLMNetworkError(LLMError):
    """网络错误 / 超时（可重试）。"""


class LLMAuthError(LLMError):
    """401/403 鉴权错误（不可重试）。"""


class LLMBadRequestError(LLMError):
    """400 参数错误（不可重试）。"""


class LLMContextTooLongError(LLMError):
    """上下文超长错误（不可重试）。"""


class LLMAbortedError(LLMError):
    """用户主动取消。"""


# ============ 令牌桶限流 ============

class TokenBucket:
    """令牌桶限流器：控制每秒最大请求数（QPS）。

    rate: 每秒放令牌数（如 2 表示每秒最多 2 次请求）
    capacity: 桶容量（允许的突发量）
    """

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, abort_event: "threading.Event | None" = None) -> bool:
        """获取一个令牌。无令牌则等待（可被 abort 中断）。返回是否获取成功。"""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._last) * self.rate,
                )
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                # 计算需要等待的时间
                wait = (1 - self._tokens) / self.rate
            # 等待期间响应 abort
            if abort_event is not None and abort_event.wait(min(wait, 0.1)):
                return False
            if abort_event is None:
                time.sleep(min(wait, 0.1))


# ============ 配置加载 ============

def _safe_close(resp):
    """安全关闭 requests 响应，吞掉所有异常（供后台线程调用）。"""
    try:
        resp.close()
    except Exception:
        pass


def _load_env():
    cfg = {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": os.environ.get("LLM_MODEL", "qwen3.8-max"),
    }
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "LLM_API_KEY":
                    cfg["api_key"] = v
                elif k == "LLM_BASE_URL":
                    cfg["base_url"] = v
                elif k == "LLM_MODEL":
                    cfg["model"] = v
    return cfg


# ============ LLM 客户端 ============

class LLMClient:
    """封装 LLM 调用的客户端，集中管理流式 / 取消 / 错误 / 重试 / 限制。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        max_retries: int = 3,
        timeout: float = 60.0,
        read_timeout: float = 45.0,   # 流式读取时单次等待数据的超时（防止服务端挂起导致永久阻塞）
        qps: float = 2.0,            # 限流：每秒最大请求数
        max_tokens: int = 0,         # 0 表示不限制上下文 token 数
    ):
        cfg = _load_env()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = (base_url or cfg["base_url"]).rstrip("/")
        self.model = model or cfg["model"]
        self.max_retries = max_retries
        self.timeout = timeout
        self.read_timeout = read_timeout
        self.max_tokens = max_tokens
        self._bucket = TokenBucket(rate=qps, capacity=max(qps, 1.0))
        self._abort_event = threading.Event()
        # 当前正在进行的流式响应对象：abort 时主动关闭它，才能真正打断阻塞中的读取
        self._current_resp = None
        self._resp_lock = threading.Lock()

    # ---- 对外接口 ----

    def abort(self):
        """取消当前及后续请求。

        关键：除了置位事件，还要主动关闭正在读取的 HTTP 连接。
        因为在流式读取中线程阻塞在 socket read 上，仅置位事件无法唤醒它。
        """
        self._abort_event.set()
        with self._resp_lock:
            resp = self._current_resp
        if resp is not None:
            # resp.close() 在另一线程正在 iter_lines 时可能短暂阻塞（requests 内部锁），
            # 放到独立线程里关闭，保证 abort() 本身立即返回。
            threading.Thread(target=_safe_close, args=(resp,), daemon=True).start()

    def reset_abort(self):
        """清空取消标记（每轮新请求开始前必须调用，否则会永久处于取消态）。"""
        self._abort_event.clear()

    def chat(self, messages, tools=None, stream=False):
        """发起一轮对话。

        stream=False：返回完整 message dict（含 content 或 tool_calls）。
        stream=True ：返回生成器，逐段 yield 文本增量（模拟打字）。
        """
        if stream:
            # 流式走独立方法（内部自己做限流与长度检查），避免重复占用令牌
            return self.chat_stream(messages, tools)

        # 发请求前做限制检查
        if not self._bucket.acquire(self._abort_event):
            raise LLMAbortedError("请求已取消（限流等待中）。")
        if self._abort_event.is_set():
            raise LLMAbortedError("请求已取消。")

        # 上下文长度检查
        self._check_context(messages)
        return self._chat_once(messages, tools)

    # ---- 内部：非流式 + 重试 ----

    def _chat_once(self, messages, tools=None):
        last_err = None
        for attempt in range(self.max_retries + 1):
            if self._abort_event.is_set():
                raise LLMAbortedError("请求已取消。")
            try:
                return self._request(messages, tools)
            except (LLMRateLimitError, LLMServerError, LLMNetworkError) as e:
                # 可重试错误：指数退避
                last_err = e
                if attempt >= self.max_retries:
                    break
                delay = 2 ** attempt  # 1s, 2s, 4s
                self._sleep_interruptible(delay)
            except LLMError:
                raise  # 不可重试，直接抛
        raise last_err

    # ---- 内部：真实 HTTP 请求（非流式）----

    def _request(self, messages, tools=None):
        payload = self._build_payload(messages, tools, stream=False)
        url = self.base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if _HAS_REQUESTS:
            # 用 stream=True 发请求：响应头会立刻返回，body 延后读取。
            # 这样可以把 resp 注册到 _current_resp，使用户在「思考中」阶段
            # 点停止时也能通过关闭连接立刻打断，而不是干等 60 秒。
            timeout = (min(self.timeout, 10.0), self.read_timeout)
            try:
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=timeout, stream=True)
            except (requests.exceptions.Timeout, OSError, ConnectionError) as e:
                raise LLMNetworkError(f"网络错误：{e}") from e

            status = resp.status_code
            with self._resp_lock:
                self._current_resp = resp
            try:
                raw = resp.content  # 读 body，可被 abort 的 close 打断
            except Exception as e:
                if self._abort_event.is_set():
                    raise LLMAbortedError("请求已取消。") from e
                if isinstance(e, (requests.exceptions.RequestException, OSError)):
                    raise LLMNetworkError(f"读取响应失败：{e}") from e
                raise
            finally:
                with self._resp_lock:
                    self._current_resp = None
                _safe_close(resp)

            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError as e:
                raise LLMNetworkError(f"响应非 JSON（可能为代理/网关错误）：{e}") from e
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = json.loads(r.read().decode("utf-8"))
                    status = 200
            except urllib.error.HTTPError as e:
                status = e.code
                body = json.loads(e.read().decode("utf-8")) if e.read else {}
            except (TimeoutError, OSError) as e:
                raise LLMNetworkError(f"网络错误：{e}") from e

        return self._parse_response(status, body)

    def chat_stream_events(self, messages, tools=None):
        """流式对话（结构化事件），一次请求同时处理文本与工具调用。

        yield 两种事件：
          ("text", 文本增量)
          ("tool_calls", [ {id, type, function:{name, arguments}} ])  —— 仅在模型要求调工具时

        相比「先非流式判断再流式输出」，这样只需一次请求：
        token 减半、延迟减半，且长回答不会把非流式请求的读超时撑爆。
        """
        if self._abort_event.is_set():
            raise LLMAbortedError("请求已取消。")
        if not self._bucket.acquire(self._abort_event):
            raise LLMAbortedError("请求已取消（限流等待中）。")
        self._check_context(messages)
        yield from self._request_stream_events(messages, tools)

    def chat_stream(self, messages, tools=None):
        """流式对话（仅文本），兼容旧接口。"""
        for kind, payload in self.chat_stream_events(messages, tools):
            if kind == "text":
                yield payload

    # ---- 内部：流式请求，解析文本与 tool_calls delta ----

    def _request_stream_events(self, messages, tools=None):
        if not _HAS_REQUESTS:
            # 无 requests 时降级：走非流式，整段吐出
            msg = self._request(messages, tools)
            if msg.get("tool_calls"):
                yield ("tool_calls", msg["tool_calls"])
            elif msg.get("content"):
                yield ("text", msg["content"])
            return

        payload = self._build_payload(messages, tools, stream=True)
        url = self.base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        # 流式：连接超时短一些，读超时给足（模型两个 token 间隔通常远小于此）
        timeout = (min(self.timeout, 10.0), self.read_timeout)
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=timeout, stream=True)
        except (requests.exceptions.Timeout, OSError, ConnectionError) as e:
            raise LLMNetworkError(f"网络错误：{e}") from e

        status = resp.status_code
        with self._resp_lock:
            self._current_resp = resp

        # 累积 tool_calls（流式下按 index 分片到达，需要拼接）
        acc = {}
        finish_reason = None
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if self._abort_event.is_set():
                    raise LLMAbortedError("请求已取消。")
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    self._handle_error(status, chunk["error"])
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}

                # 文本增量
                if delta.get("content"):
                    yield ("text", delta["content"])

                # 工具调用增量：按 index 聚合 id / name / arguments 分片
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = acc.setdefault(
                        idx, {"id": "", "type": "function",
                              "function": {"name": "", "arguments": ""}}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

            if status >= 400:
                self._handle_error(status, {"message": f"HTTP {status}"})
        except LLMAbortedError:
            raise
        except Exception as e:
            if self._abort_event.is_set():
                raise LLMAbortedError("请求已取消。") from e
            if isinstance(e, (requests.exceptions.RequestException, OSError)):
                raise LLMNetworkError(f"流式读取中断：{e}") from e
            raise
        finally:
            with self._resp_lock:
                self._current_resp = None
            _safe_close(resp)

        # 流结束后，如果模型要求调工具，统一吐出一个事件
        if acc:
            ordered = [acc[i] for i in sorted(acc)]
            for i, call in enumerate(ordered):
                if not call["id"]:
                    call["id"] = f"call_{int(time.time()*1000)}_{i}"
            yield ("tool_calls", ordered)


    # ---- 内部：工具方法 ----

    def _build_payload(self, messages, tools, stream):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _parse_response(self, status, body):
        if status == 200:
            return body["choices"][0]["message"]
        if "error" in body:
            self._handle_error(status, body["error"])
        self._handle_error(status, {"message": f"HTTP {status}"})

    def _handle_error(self, status, err):
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if status == 401 or status == 403:
            raise LLMAuthError(f"鉴权失败({status})：{msg}。请检查 API Key。")
        if status == 429:
            raise LLMRateLimitError(f"触发限流(429)：{msg}。")
        if status == 400:
            if "context" in msg.lower() or "token" in msg.lower() or "length" in msg.lower():
                raise LLMContextTooLongError(f"上下文超长(400)：{msg}。")
            raise LLMBadRequestError(f"请求参数错误(400)：{msg}。")
        if status >= 500:
            raise LLMServerError(f"服务端错误({status})：{msg}。")
        raise LLMError(f"未知错误({status})：{msg}。")

    def _check_context(self, messages):
        if not self.max_tokens:
            return
        total = sum(len(m.get("content", "") or "") for m in messages)
        # 粗略估算：1 个 token ≈ 2 个汉字或 4 个英文字符
        est_tokens = total // 2
        if est_tokens > self.max_tokens:
            raise LLMContextTooLongError(
                f"上下文约 {est_tokens} token，超过上限 {self.max_tokens}。请精简历史消息。"
            )

    def _sleep_interruptible(self, seconds):
        """可被 abort 中断的等待。"""
        if self._abort_event.wait(seconds):
            raise LLMAbortedError("请求已取消。")


# ============ 便捷函数（兼容旧接口）============

_default_client = None
_client_lock = threading.Lock()


def _get_default_client():
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = LLMClient()
        return _default_client


def chat(messages, tools=None):
    """非流式对话（兼容旧代码）。"""
    return _get_default_client().chat(messages, tools=tools)


def chat_stream(messages, tools=None):
    """流式对话（仅文本），返回生成器。"""
    return _get_default_client().chat_stream(messages, tools)


def chat_stream_events(messages, tools=None):
    """流式对话（结构化事件），一次请求同时处理文本与 tool_calls。

    yield ("text", 文本增量) 或 ("tool_calls", [工具调用列表])
    """
    return _get_default_client().chat_stream_events(messages, tools)


def abort():
    """取消默认客户端的当前请求。"""
    _get_default_client().abort()
