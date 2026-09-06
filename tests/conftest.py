"""テストは外部APIへ接続せず、OpenAI境界は各テストで明示的にモックする。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_external_ai_for_api_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカル.envの課金設定が回帰テストへ影響しないようにする。"""

    monkeypatch.setenv("AI_PROVIDER", "demo")
    monkeypatch.setenv("IMAGE_PROVIDER", "demo")
