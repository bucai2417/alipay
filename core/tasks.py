"""后台任务线程池：商户回调、邮件等不阻塞请求与监控循环。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.logger import get_logger

logger = get_logger("tasks")

# 回调 / 通知等通用后台任务
_background = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bg")
# 邮件单独限流，避免同一 SMTP 账号并发连接过多
_mail = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")


def _wrap(name, fn, args, kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as e:
        logger.warning("后台任务失败 %s: %s", name, e)


def submit_background(fn, *args, **kwargs) -> None:
    name = getattr(fn, "__name__", str(fn))
    _background.submit(_wrap, name, fn, args, kwargs)


def submit_mail(fn, *args, **kwargs) -> None:
    name = getattr(fn, "__name__", str(fn))
    _mail.submit(_wrap, name, fn, args, kwargs)
