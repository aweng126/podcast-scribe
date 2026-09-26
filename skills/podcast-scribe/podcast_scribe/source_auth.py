"""Explicit, lazy Bilibili cookie access; credentials remain only in memory."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import copy
import io
from pathlib import Path
import time

from .model import ContentError

SUPPORTED_BROWSERS = frozenset({"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"})


class CookieAccessError(ContentError):
    pass


class _LoadLogger:
    """Keep only a failure flag, never cookie extraction messages or paths."""
    failed = False

    def debug(self, message):
        if "could not be decrypted" in str(message).lower():
            self.failed = True

    def warning(self, message):
        text = str(message).lower()
        if any(word in text for word in ("decrypt", "failed", "cannot", "could not", "unsupported")):
            self.failed = True

    def error(self, _message):
        self.failed = True


class SourceAuth:
    """One explicitly selected source, loaded once for inspect and download."""

    def __init__(self, *, browser: str | None = None, cookies: Path | None = None):
        if bool(browser) == (cookies is not None):
            raise CookieAccessError("请仅选择一种登录态来源：--cookies-from-browser 或 --cookies。")
        self._browser = None
        if browser:
            name, separator, profile = browser.partition(":")
            name = name.lower()
            if name not in SUPPORTED_BROWSERS or (separator and (not profile.strip() or "::" in browser)):
                raise CookieAccessError("浏览器参数应为 BROWSER[:PROFILE]；本版不支持 KEYRING 或 CONTAINER 扩展语法。")
            self._browser = (name, profile if separator else None, None, None)
        self._cookies = Path(cookies).expanduser() if cookies is not None else None
        self._jar = None
        self._failure = None

    def attach(self, ydl):
        """Attach a domain-filtered memory jar without setting cookiefile."""
        if self._failure is not None:
            raise CookieAccessError(self._failure)
        if self._jar is None:
            try:
                self._jar = self._load(ydl)
            except CookieAccessError as exc:
                self._failure = str(exc)
                raise
        self._jar.clear_expired_cookies()
        if not len(self._jar):
            raise CookieAccessError("授权的 B站 cookies 已过期或不可用；请在浏览器重新登录并重新授权。未匿名继续。")
        # YoutubeDL.close() writes cookies only when cookiefile is not None.
        ydl.params["cookiefile"] = None
        ydl.params["cookiesfrombrowser"] = None
        ydl.cookiejar = self._jar

    def _load(self, ydl):
        logger = _LoadLogger()
        original_logger = ydl.params.get("logger")
        output = io.StringIO()
        try:
            from yt_dlp.cookies import YoutubeDLCookieJar, load_cookies
            if self._cookies is not None and not self._cookies.is_file():
                raise CookieAccessError("无法读取授权的 cookies 文件；请检查文件是否存在、访问权限及 Netscape 格式。")
            ydl.params["logger"] = logger
            # The loader can print malformed Netscape lines directly, including
            # cookie values, without using the logger. Never echo those lines.
            with redirect_stderr(output), redirect_stdout(output):
                loaded = load_cookies(str(self._cookies) if self._cookies is not None else None,
                                      self._browser, ydl)
            if output.tell() or logger.failed:
                raise CookieAccessError(self._load_failure())
            filtered = YoutubeDLCookieJar()
            now = time.time()
            for cookie in loaded:
                domain = cookie.domain.lstrip(".").lower()
                if (domain == "bilibili.com" or domain.endswith(".bilibili.com")) and not cookie.is_expired(now):
                    filtered.set_cookie(copy(cookie))
            if not len(filtered):
                raise CookieAccessError("授权来源中没有可用的未过期 B站 cookies；请先在所选浏览器正常访问 B站，必要时重新登录并重新授权。")
            return filtered
        except CookieAccessError:
            raise
        except Exception:
            raise CookieAccessError(self._load_failure()) from None
        finally:
            ydl.params["logger"] = original_logger
            output.close()

    def _load_failure(self):
        if self._browser is not None:
            return "无法完整读取或解密所选浏览器的 cookies；请检查浏览器配置、系统授权及密钥环支持，或显式提供 Netscape cookies 文件。未改用其他浏览器或匿名继续。"
        return "无法完整读取授权的 cookies 文件；请检查访问权限、Netscape 格式和有效期，重新导出后再试。未匿名继续。"
