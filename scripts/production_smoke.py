"""デプロイ後の公開HTTP到達性と静的アセットを確認する軽量Smoke Check。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 10


def normalize_base_url(value: str) -> str:
    """HTTP(S)の公開URLだけを受け取り、末尾スラッシュを統一する。"""

    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("公開URLにはhttpまたはhttpsのURLを指定してください")
    return base_url


def fetch(base_url: str, path: str) -> Tuple[int, bytes]:
    """指定パスへGETし、本文を必要最小限だけ返す。"""

    request = Request(
        base_url + path,
        headers={"Accept": "application/json, text/html, */*"},
        method="GET",
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return int(response.status), response.read()


def run_check(base_url: str, path: str, parser=None) -> Dict[str, Any]:
    """1つの公開エンドポイントを確認し、秘密情報を含めず結果を返す。"""

    try:
        status_code, body = fetch(base_url, path)
        result: Dict[str, Any] = {
            "ok": status_code == 200,
            "status_code": status_code,
        }
        if parser:
            result.update(parser(body))
        if not result["ok"] and "error" not in result:
            result["error"] = "HTTP status is not 200"
        return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:240]}


def parse_health(body: bytes) -> Dict[str, Any]:
    """Healthレスポンスの状態だけを取り出す。"""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "health response was not valid JSON"}
    return {
        "ok": payload.get("status") == "ok",
        "service": payload.get("service"),
        "status": payload.get("status"),
    }


def parse_asset(body: bytes) -> Dict[str, Any]:
    """静的ファイルが空でないことだけを確認する。"""

    return {"ok": bool(body), "bytes": len(body)}


def main() -> int:
    """Smoke Check結果をJSONで出力する。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="デプロイ済みアプリの公開URL")
    args = parser.parse_args()

    try:
        base_url = normalize_base_url(args.url)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    checks = {
        "health": run_check(base_url, "/api/health", parse_health),
        "login": run_check(base_url, "/login", parse_asset),
        "css": run_check(base_url, "/static/css/app.css?v=6", parse_asset),
        "javascript": run_check(base_url, "/static/js/app.js?v=8", parse_asset),
    }
    result = {"ok": all(check["ok"] for check in checks.values()), "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
