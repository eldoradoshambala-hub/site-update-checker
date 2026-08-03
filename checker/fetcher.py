"""サイトのHTML取得。

エンコーディングは日本語サイトで誤判定しやすいので、本文はバイト列のまま返して
パーサ側（extractor）に meta charset を読ませる。HTTPヘッダの charset はヒントとして渡す。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

# 一時的な失敗として再試行する対象。
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FetchResult:
    """取得結果。``error`` が None でなければ失敗。"""

    url: str
    content: bytes = b""
    encoding_hint: str | None = None
    status: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _charset_from_headers(response: requests.Response) -> str | None:
    content_type = response.headers.get("Content-Type", "")
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip('"\'')
            # requests は charset 未指定の text/* に ISO-8859-1 を当ててしまうため無視する。
            if charset and charset.lower() != "iso-8859-1":
                return charset
    return None


def fetch(
    url: str,
    *,
    timeout: int = 20,
    user_agent: str,
    retries: int = 2,
    session: requests.Session | None = None,
    sleep: float = 1.5,
) -> FetchResult:
    """URL を取得する。一時的なエラーは指数バックオフで再試行する。"""
    http = session or requests.Session()
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    }

    last_error = "不明なエラー"
    last_status: int | None = None

    for attempt in range(retries + 1):
        if attempt:
            time.sleep(sleep * (2 ** (attempt - 1)))
        try:
            response = http.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except requests.Timeout:
            last_error = f"タイムアウト（{timeout}秒）"
            continue
        except requests.RequestException as exc:
            last_error = f"接続エラー: {exc.__class__.__name__}"
            continue

        last_status = response.status_code
        if response.status_code in _RETRY_STATUS:
            last_error = f"HTTP {response.status_code}"
            continue
        if response.status_code >= 400:
            return FetchResult(
                url=response.url,
                status=response.status_code,
                error=f"HTTP {response.status_code}",
            )

        return FetchResult(
            url=response.url,
            content=response.content,
            encoding_hint=_charset_from_headers(response),
            status=response.status_code,
        )

    return FetchResult(url=url, status=last_status, error=last_error)
