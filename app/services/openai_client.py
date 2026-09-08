"""OpenAI公式REST APIへ接続するサーバー側の小さな共通クライアント。

既存アプリは外部依存を増やさずに動作する構成のため、OpenAI Python SDKへ
全面移行せず、Responses APIとImages APIのHTTP境界だけをここへ集約する。
APIキーや本文をログへ出さないこと、再試行回数を限定することを優先する。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Mapping, Optional


class OpenAIRequestError(RuntimeError):
    """OpenAIリクエストに失敗した。メッセージへ秘密や本文を含めない。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        error_code: Optional[str] = None,
        error_type: Optional[str] = None,
        category: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.error_code = error_code
        self.error_type = error_type
        self.category = category


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _http_error_category(
    status_code: int,
    error_code: Optional[str] = None,
    error_type: Optional[str] = None,
) -> str:
    """HTTP応答を再試行可否の判断に使える安全な分類へ変換する。"""

    code = str(error_code or "").lower()
    error_kind = str(error_type or "").lower()
    if status_code == 401:
        return "authentication"
    if status_code == 403 and ("model" in code or "model" in error_kind):
        return "model_access"
    if status_code == 403:
        return "permission"
    if status_code == 404 and (
        "model" in code or "model" in error_kind
    ):
        return "model_access"
    if status_code == 404:
        return "request"
    if status_code == 408:
        return "timeout"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        if code in {"insufficient_quota", "quota_exceeded", "billing_hard_limit_reached"}:
            return "quota"
        return "rate_limit"
    if status_code >= 500:
        return "server"
    if status_code == 400:
        return "request"
    return "api"


def _status_message(status_code: int, category: str) -> str:
    if category == "authentication":
        return "OpenAI APIキーまたはProject権限を確認してください"
    if category == "permission":
        return "OpenAI Projectまたはモデルへのアクセス権限を確認してください"
    if category == "model_access":
        return "選択したOpenAIモデルをこのアカウントでは利用できません"
    if category == "quota":
        return "OpenAI APIの利用上限を確認してください"
    if category == "rate_limit":
        return "OpenAI APIが一時的に混雑しています。しばらくして再試行してください"
    if category == "timeout":
        return "OpenAI APIの応答がタイムアウトしました。しばらくして再試行してください"
    if category == "conflict":
        return "OpenAI APIのリクエストが競合しました。しばらくして再試行してください"
    if status_code in {400, 404}:
        return "OpenAI APIへのリクエスト設定を確認してください"
    if status_code >= 500:
        return "OpenAI APIで一時的な障害が発生しました"
    return "OpenAI APIリクエストに失敗しました"


def _safe_error_fields(exc: urllib.error.HTTPError) -> tuple[Optional[str], Optional[str]]:
    """エラー本文から分類に必要なコードだけを取り出す。本文は保持しない。"""

    try:
        raw = exc.read(16_384)
        body = json.loads(raw.decode("utf-8"))
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return None, None
        code = error.get("code")
        error_type = error.get("type")
        return (
            str(code)[:120] if isinstance(code, str) else None,
            str(error_type)[:120] if isinstance(error_type, str) else None,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
        return None, None


def request_bytes(
    url: str,
    *,
    api_key: str,
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 90.0,
    max_retries: int = 1,
    client_request_id: Optional[str] = None,
) -> bytes:
    """JSON POSTまたはGETを実行し、レスポンスbytesを返す。

    429/5xx/通信タイムアウトだけを最大2回まで再試行する。レスポンス本文は
    エラーメッセージへ含めないため、物語やプロバイダの詳細が漏れない。
    """

    if payload is not None and not api_key:
        raise OpenAIRequestError(
            "OpenAI APIキーが設定されていません",
            category="authentication",
        )
    attempts = max(1, min(int(max_retries) + 1, 3))
    request_body = None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # OpenAI側の到達確認とサポート調査に使える相関ID。秘密や本文は含めない。
    request_id = client_request_id or str(uuid.uuid4())
    headers["X-Client-Request-Id"] = request_id
    method = "GET"
    if payload is not None:
        request_body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"

    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            data=request_body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            error_code, error_type = _safe_error_fields(exc)
            category = _http_error_category(exc.code, error_code, error_type)
            # quota枯渇は再試行しても回復しないため、課金APIを余分に呼ばない。
            retryable = _retryable_status(exc.code) and category != "quota"
            if retryable and attempt < attempts - 1:
                time.sleep(min(2.0, 0.4 * (2**attempt)))
                continue
            raise OpenAIRequestError(
                _status_message(exc.code, category),
                status_code=exc.code,
                retryable=retryable,
                error_code=error_code,
                error_type=error_type,
                category=category,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < attempts - 1:
                time.sleep(min(2.0, 0.4 * (2**attempt)))
                continue
            reason = getattr(exc, "reason", None)
            timeout_error = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
                reason, (TimeoutError, socket.timeout)
            ) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
            raise OpenAIRequestError(
                (
                    "OpenAI APIの応答がタイムアウトしました。しばらくして再試行してください"
                    if timeout_error
                    else "OpenAI APIへ接続できませんでした。ネットワークを確認して再試行してください"
                ),
                retryable=True,
                category="timeout" if timeout_error else "connection",
            ) from exc

    raise OpenAIRequestError("OpenAI APIリクエストに失敗しました", category="unknown")


def request_json(
    url: str,
    *,
    api_key: str,
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 90.0,
    max_retries: int = 1,
    client_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """JSON APIを呼び出し、オブジェクト形式のレスポンスを返す。"""

    try:
        raw = request_bytes(
            url,
            api_key=api_key,
            payload=payload,
            timeout=timeout,
            max_retries=max_retries,
            client_request_id=client_request_id,
        )
        body = json.loads(raw.decode("utf-8"))
    except OpenAIRequestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise OpenAIRequestError(
            "OpenAI APIのレスポンスをJSONとして読めませんでした",
            category="response",
        ) from exc
    if not isinstance(body, dict):
        raise OpenAIRequestError("OpenAI APIのレスポンス形式が不正です", category="response")
    return body


def response_output_text(body: Mapping[str, Any]) -> str:
    """Responses APIの出力からテキスト部分だけを抽出する。

    SDKの``output_text``相当と、RESTレスポンスのoutput/content配列の両方を
    受け付ける。画像生成出力などテキスト以外は無視する。
    """

    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = body.get("output")
    if not isinstance(output, list):
        return ""
    parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "output_text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                if block.get("type") in {None, "output_text"} or "text" in block:
                    parts.append(block["text"])
    return "\n".join(parts).strip()


def parse_json_text(text: str) -> Dict[str, Any]:
    """モデル出力をJSONオブジェクトへ変換する。Markdown囲みだけ許容する。"""

    candidate = str(text or "").strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OpenAIRequestError(
            "AIから受け取ったJSONを検証できませんでした",
            category="response",
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenAIRequestError(
            "AIから受け取ったJSONオブジェクトが不正です",
            category="response",
        )
    return parsed
