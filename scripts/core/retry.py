"""通用退避重试"""
import time


def retry_with_backoff(fn, attempts: int = 3, base_delay: float = 2.0):
    """指数退避重试：失败后等待 base_delay * 2^i 秒再试；attempts 次后抛出最后异常"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last
