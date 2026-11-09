"""公开查询/客服接口防护：IP 限流 + 一次性页面令牌。"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque


class PublicGuard:
    def __init__(self, max_requests=8, window_seconds=60, ban_seconds=300,
                 token_ttl=1800, feedback_max_requests=40, feedback_window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.ban_seconds = ban_seconds
        self.token_ttl = token_ttl
        self.feedback_max_requests = feedback_max_requests
        self.feedback_window_seconds = feedback_window_seconds
        self._lock = threading.Lock()
        self._hits = defaultdict(deque)
        self._fb_hits = defaultdict(deque)
        self._ban_until = {}
        self._tokens = {}

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._tokens[token] = now + self.token_ttl
            if len(self._tokens) > 5000:
                for k in [k for k, exp in self._tokens.items() if exp < now]:
                    self._tokens.pop(k, None)
        return token

    def validate_token(self, token) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._tokens.get(token)
            if not exp:
                return False
            if exp < time.time():
                self._tokens.pop(token, None)
                return False
            return True

    def check(self, ip: str, kind: str = "search"):
        """返回 (allowed, reason)。kind: search | feedback"""
        ip = (ip or "unknown").strip() or "unknown"
        now = time.time()
        with self._lock:
            if self._ban_until.get(ip, 0) > now:
                return False, f"请求过于频繁，请 {int(self._ban_until[ip] - now)} 秒后再试"

            if kind == "feedback":
                q = self._fb_hits[ip]
                window, max_req = self.feedback_window_seconds, self.feedback_max_requests
            else:
                q = self._hits[ip]
                window, max_req = self.window_seconds, self.max_requests

            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= max_req:
                self._ban_until[ip] = now + (self.ban_seconds if kind != "feedback" else 60)
                return False, "请求过于频繁，请稍后再试"
            q.append(now)
            return True, ""


guard = PublicGuard()
