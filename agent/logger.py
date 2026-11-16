"""统一日志：控制台 + 按大小轮转的文件（agent/logs/app.log）。

用法：
    from logger import get_logger
    log = get_logger(__name__)
    log.info("...")

设计：
- 只配置一次（进程级单例），重复导入不会重复加 handler；
- 文件 5MB × 3 份轮转，避免日志文件无限增长；
- LOG_DIR 可用环境变量覆盖（测试时指向临时目录）。
"""
import logging
import logging.handlers
import os

LOG_DIR = os.environ.get(
    "LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
)
LOG_PATH = os.path.join(LOG_DIR, "app.log")

_ROOT_NAME = "agent"
_configured = False


def _setup():
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(logging.INFO)
    root.propagate = False  # 不向 root logger 冒泡，避免第三方库重复输出

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # 文件（轮转）
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        # 目录不可写时退化为仅控制台，不影响主流程
        pass


def get_logger(name: str = "agent") -> logging.Logger:
    """取一个挂在统一配置下的 logger。name 建议传模块名。"""
    _setup()
    short = name.rsplit(".", 1)[-1] if name else "agent"
    return logging.getLogger(f"{_ROOT_NAME}.{short}")
