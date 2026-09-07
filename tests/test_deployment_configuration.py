"""GitHub ActionsとRender自動デプロイ設定の契約テスト。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RENDER_YAML = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
CI_WORKFLOW = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_render_uses_main_checks_pass_and_server_side_openai_secret() -> None:
    """RenderがmainのCI成功後だけデプロイし、キー値をBlueprintへ持たないことを確認する。"""

    assert "branch: main" in RENDER_YAML
    assert "autoDeployTrigger: checksPass" in RENDER_YAML
    assert "- key: OPENAI_API_KEY\n        sync: false" in RENDER_YAML
    assert "OPENAI_API_KEY=sk-" not in RENDER_YAML


def test_ci_workflow_contains_required_validation_gates() -> None:
    """CIがpush/PRの両方で依存関係、テスト、compile、JS構文を検証することを確認する。"""

    assert "push:" in CI_WORKFLOW
    assert "pull_request:" in CI_WORKFLOW
    assert "branches:" in CI_WORKFLOW
    assert "python -m pip install -r requirements.txt" in CI_WORKFLOW
    assert "python -m pip check" in CI_WORKFLOW
    assert "python -m pytest -q" in CI_WORKFLOW
    assert "python -m compileall -q app" in CI_WORKFLOW
    assert "node --check static/js/app.js" in CI_WORKFLOW
