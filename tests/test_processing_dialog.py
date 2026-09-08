"""長時間処理用Processing Dialogの共有契約を確認するテスト。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATE = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
APP_SCRIPT = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_STYLES = (PROJECT_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_processing_dialog_is_shared_and_accessible() -> None:
    """全画面で再利用するダイアログがアクセシブルな属性を持つことを確認する。"""

    assert 'id="processing-dialog-root" hidden aria-hidden="true"' in BASE_TEMPLATE
    assert 'id="processing-dialog" role="dialog" aria-modal="true"' in BASE_TEMPLATE
    assert 'aria-labelledby="processing-dialog-title"' in BASE_TEMPLATE
    assert 'aria-describedby="processing-dialog-message processing-dialog-progress processing-dialog-submessage"' in BASE_TEMPLATE
    assert 'id="processing-dialog-root" hidden aria-hidden="true" aria-busy="false"' in BASE_TEMPLATE
    assert 'id="processing-dialog-message" aria-live="polite"' in BASE_TEMPLATE
    assert 'id="processing-dialog-progress" aria-live="polite"' in BASE_TEMPLATE
    assert 'class="processing-spinner"' in BASE_TEMPLATE


def test_processing_dialog_covers_long_operations_and_cleans_up() -> None:
    """主要な非同期処理が共通表示を使い、例外時にも閉じることを確認する。"""

    messages = (
        "物語を取り込んでいます…",
        "ナレッジを処理しています…",
        "物語を解析しています…",
        "キャラクター設定を生成しています…",
        "ストーリーボードを生成しています…",
        "漫画画像を生成しています…",
        "漫画画像を再生成しています…",
        "QAを実行しています…",
        "PDFを書き出しています…",
        "ZIPを書き出しています…",
    )
    for message in messages:
        assert message in APP_SCRIPT

    assert "function showProcessingDialog(options)" in APP_SCRIPT
    assert "function updateProcessingDialog(options)" in APP_SCRIPT
    assert "function hideProcessingDialog()" in APP_SCRIPT
    assert "finally {" in APP_SCRIPT
    assert "hideProcessingDialog();" in APP_SCRIPT
    assert "processingDialogElements.root.hidden = false" in APP_SCRIPT
    assert "processingDialogElements.root.hidden = true" in APP_SCRIPT
    assert 'document.body.classList.add("processing-dialog-open")' in APP_SCRIPT
    assert 'document.body.classList.remove("processing-dialog-open")' in APP_SCRIPT


def test_generation_progress_uses_job_state_and_preserves_selective_retry() -> None:
    """パネル進捗がJob状態由来で、対象コマだけを追跡することを確認する。"""

    assert "const queuedPanelIds = Array.isArray(data.queued_panel_ids)" in APP_SCRIPT
    assert "const targetIds = new Set(targetPanelIds || [])" in APP_SCRIPT
    assert 'panel.status === "completed"' in APP_SCRIPT
    assert 'panel.status === "failed"' in APP_SCRIPT
    assert '"コマ中 " + current + "コマ目を生成しています"' in APP_SCRIPT
    assert '"失敗したコマは生成画面から再試行できます。"' in APP_SCRIPT
    assert "queueGeneration([button.dataset.retryPanel], true, true)" in APP_SCRIPT


def test_processing_dialog_blocks_escape_and_reduces_motion() -> None:
    """処理中の誤操作を抑止し、Reduced Motion設定を尊重することを確認する。"""

    assert 'event.key === "Escape" || event.key === "Tab"' in APP_SCRIPT
    assert "event.preventDefault();" in APP_SCRIPT
    assert '@media (prefers-reduced-motion: reduce)' in APP_STYLES
    assert ".processing-dialog-root[hidden] { display: none; }" in APP_STYLES
    assert "place-items: center" in APP_STYLES


def test_mobile_global_theme_control_remains_compact_and_in_header() -> None:
    """狭い画面でテーマ操作が縦に潰れず、header右端へ留まることを確認する。"""

    assert ".sidebar-bottom { display: flex; grid-column: 2; grid-row: 1;" in APP_STYLES
    assert ".sidebar-bottom .theme-nav { width: 36px; overflow: hidden; padding: 0; font-size: 0; }" in APP_STYLES
