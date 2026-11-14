# 调试文档（DEBUGGING）

本项目在 Windows + WorkBuddy 沙箱环境中开发，有几个反复踩的环境特性，
沉淀成下面这套调试方法。**新接手的人先读「环境适配」，再看「通用流程」。**

---

## 一、环境适配（本机特有，每条都对应过实际事故）

### 1. 两个 shell 的分工

| 工具 | 适合 | 不适合 |
|---|---|---|
| **Bash** | 一切需要**读输出**的操作（git 状态、日志、grep） | 杀 Windows 进程 |
| **PowerShell** | 杀进程、启服务、`netstat` | 读输出（**会吞 stdout**，exit 0 但啥都没有，见 ERRORS E15） |

### 2. Bash 必须先补 PATH

Bash shim 缺 coreutils，且 **shell 状态不跨调用保留**，所以**每条命令**开头都要：

```bash
export PATH="/c/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:/c/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/mingw64/bin:/c/Windows/System32:$PATH"
```

忘了补的典型症状：`dirname: command not found`、`head: command not found`。

### 3. 输出闭环必须 UTF-8

排查套路是「命令 → 写文件 → Read 工具读」，但 Windows PowerShell 默认
`Out-File` 是 UTF-16 LE，读出来全是乱码（E12）。两种解法：

- 让被测脚本**自己**用 UTF-8 写结果文件（推荐，Python 里 `open(..., encoding="utf-8")`）；
- 或事后转一道：`Get-Content x.txt -Encoding Unicode | Out-File x2.txt -Encoding utf8`。

另外跑 Python 尽量带 `-X utf8`，避免 print 时撞上 GBK 控制台。

### 4. Python 用哪个

- 项目运行：`py` 启动器（实际 `D:\python\python.exe`，3.13.5）。
- 沙箱内辅助脚本：托管解释器
  `C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`。

### 5. 本机网络两条硬规则

1. **访问 127.0.0.1 必须显式禁用代理**（E8）：
   `urllib.request.build_opener(urllib.request.ProxyHandler({}))`
2. **`api.github.com` 通，`github.com:443` 被代理拦截**——git push 不通≠网络不通，
   要按域名分别探测（详见本文档末节）。

### 6. 服务进程的唯一可信状态是「端口 + PID」

Windows 下 `http.server` 的 `allow_reuse_address` 允许**多进程绑同一端口**（E13），
所以：

```powershell
netstat -ano | findstr :8000          # 找到所有监听 PID
Stop-Process -Id <pid> -Force         # 逐个杀
# 再启动，并再次 netstat 确认只剩一个新 PID
```

「启动了」不算数，「只有一个预期 PID 在监听」才算数。
另外：**别用 `Start-Process -RedirectStandardOutput` 起服务**（E14，会静默死亡），
用「后台任务 + 前台运行 + Out-File」。

### 7. UI 验证用无头 Chrome 截图

改 `index.html` 后，用无头 Chrome 截图核对（空态 / 会话态 / 工具卡片 / 窄屏），
注意**最小视口 500px**，更小的宽度截图会变形。

---

## 二、通用调试流程

### 第 0 步：先怀疑测试代码

E7 的教训：E2E 断言失败，实际是测试脚本变量复用的 bug，服务没问题。
**排查顺序：测试脚本 → 测试环境（代理/编码/进程残留）→ 服务代码。**

### 第 1 步：最小探针（probe script）

怀疑哪个环节，就写个几十行的独立脚本**只验证那一件事**，跑出事实再改代码。
本项目用过的探针：搜索引擎可用性探测、结果解析探测、跳转链接行为探测、
Bing 查询相关性探测、代理 CONNECT 分域名探测。

探针命名 `_xxx_probe.py`，用完即删——它们是一次性侦察兵，不进仓库。

### 第 2 步：区分"程序错误"与"数据错误"

- 程序错误（E2 的 400）：看异常栈 + 请求/响应原文。
- 数据错误（E11 的搜索跑偏）：把**中间产物**打出来看——清洗后的查询词、
  引擎原始返回、过滤前后的结果集。数据问题从来不是"代码挂了"，
  而是"每一步都对、合起来不对"，必须逐段看数据。

### 第 3 步：E2E 用真 LLM 跑，不 mock

本项目所有关键链路（记忆、停止、技能触发、联网兜底）都做过真实 LLM 的 E2E。
原因是这些功能的 bug 大多出在「工具返回 → 模型决策」的交界处，mock 掉模型
就等于 mock 掉了 bug 本身。代价是每次 5-20s，可接受。

### 第 4 步：修复后必留"防回归证据"

每个修复对应一份可重复执行的验证：单测（`test_*.py`）或 E2E 记录。
修一个 bug 不加测试，等于没修（参见 ERRORS 里每条的「验证」段）。

---

## 三、分领域速查

### SSE / 流式问题

1. 先确认「浏览器怎么知道流结束了」——`Content-Length`、`chunked`、
   `Connection: close` 三选一，缺一不可（E1）。
2. 取消机制要有两层：标志位（协作式）+ 主动打断 IO（强制式）。
3. 前端要有「本地立即解锁」和看门狗，不把解锁寄托在服务端的及时响应上。

### LLM 消息序列问题

1. 400 先看消息配对：`assistant(tool_calls)` 后面必须紧跟等量的 `tool` 消息。
2. 改 system prompt 不许动历史（E2）。
3. 长上下文问题查 `context.py` 四层压缩各自生效没有：
   工具结果落盘 → tool 消息简化 → 会话总结 → 裁剪兜底。

### 工具结果"丢失"问题

症状：模型说"没查到"，其实工具返回了内容。
查 `maybe_dump_tool_result` 的豁免名单——**指令性结果**（read_file / use_skill /
read_skill_file / web_search / fetch_url）必须豁免落盘，否则模型只看到
一个文件路径就会据此"总结"，内容全丢。

### 联网检索质量问题

按 E9-E11 的顺序查：限流？（换引擎/缓存）→ 查询词脏？（清洗）→
核心词跑偏？（核心词过滤 + 两档 + 收敛）。**每加一层过滤都要在结果里明示置信度**，
让模型知道能不能直接信。

---

## 四、案例：github.com:443 被代理拦截时如何推送

完整记录（2026-11-14 实战）：

1. **分域名探测**，不要整体判死刑：

   ```python
   import socket
   for host in ("github.com", "api.github.com", "ssh.github.com", "codeload.github.com"):
       s = socket.create_connection(("127.0.0.1", 50340), timeout=8)
       s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
       print(host, s.recv(80).split(b"\r\n")[0]); s.close()
   ```

   结果：`github.com` 超时，`api.github.com` / `codeload` / `ssh.github.com` 通。

2. **先试 SSH over 443**（标准兜底）：能建隧道，但服务端 banner 异常、
   KEXINIT 卡死（E18）→ 判定中间设备干扰，**放弃，不纠缠**。

3. **改走 REST API 手工推送**（关键收益：远程 SHA 可以与本地完全一致）：
   - `git cat-file commit HEAD` 取原始 tree / author / committer / message；
   - 逐文件 `POST /git/blobs`（base64），校验返回 sha == 本地 blob sha；
   - tree 自底向上逐目录 `POST /git/trees`，根目录最后，校验根 tree sha；
   - `POST /git/commits`（`parents: []`）→ `POST /git/refs` 或
     `PATCH /git/refs/heads/main`（force）；
   - 断言 commit sha == `git rev-parse HEAD`。

4. 遇到的坑与解法全部记录在 `docs/ERRORS.md` E19（空仓库 409、
   quotepath、ISO 日期、update-ref 失效）。

5. **推送后独立校验**（不看命令返回码）：
   `GET /repos/{o}/{r}/branches/main` 对 SHA，
   `GET /repos/{o}/{r}/git/trees/main?recursive=1` 数文件并复查有无敏感文件。

6. 临时令牌文件用完即删；`.git/config` 里 grep 确认无令牌残留。
