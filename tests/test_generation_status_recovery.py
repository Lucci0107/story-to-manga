"""ブラウザの状態取得を実行し、通信障害時にも生成POSTを再送しないことを確認する。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text(encoding="utf-8")
API = "class WorkspaceApiError" + SCRIPT.split("class WorkspaceApiError", 1)[1].split("async function fetchProject(", 1)[0]
STATUS = "function panelStatusUrl()" + SCRIPT.split("function panelStatusUrl()", 1)[1].split("function clearPanelPollingTimer(", 1)[0]
RECOVERY = "function panelRecoveryKind(error)" + SCRIPT.split("function panelRecoveryKind(error)", 1)[1].split("function showPanelRecoveryNotice(", 1)[0]


def _request_status(responses: list, *, retry: bool = True, aborted: bool = False) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("ブラウザJavaScriptの実行にはNode.jsが必要です")
    harness = r"""
      const fs = require('node:fs');
      const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
      const state = {id: 'fixture-project'};
      const calls = [], waits = [];
      const window = {
        setTimeout(callback, delay) {
          if (delay !== 10000) { waits.push(delay); callback(); }
          return 1;
        },
        clearTimeout() {}
      };
      async function fetch(url, options) {
        calls.push({url, method: options.method || 'GET', body: options.body || null});
        const response = fixture.responses[Math.min(calls.length - 1, fixture.responses.length - 1)];
        if (response === 'network') throw new TypeError('fixture network failure');
        if (response === 'timeout') {
          const error = new Error('fixture timeout'); error.name = 'AbortError'; throw error;
        }
        const status = typeof response === 'number' ? response : 200;
        const body = response === 'invalid' ? '<html>fixture</html>'
          : response === 'empty' ? '{}'
          : JSON.stringify({panels: [{id: 'panel-1', status: 'not_started'}], panel_generation: {active: false}});
        return {ok: status < 400, status, text: async () => {
          if (response === 'body_network') throw new TypeError('fixture interrupted response');
          if (response === 'body_timeout') {
            const error = new Error('fixture response timeout'); error.name = 'AbortError'; throw error;
          }
          return status >= 500 ? 'fixture gateway failure' : body;
        }};
      }
      (async () => {
        const controller = new AbortController();
        if (fixture.aborted) controller.abort();
        const options = {retry: fixture.retry, signal: controller.signal};
        let result;
        try { result = {data: await fetchPanelGenerationStatus(options)}; }
        catch (error) { result = {error: {status: error.status, category: error.category, kind: panelRecoveryKind(error)}}; }
        process.stdout.write(JSON.stringify({...result, calls, waits}));
      })().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
    """
    result = subprocess.run(
        [node, "-e", API + STATUS + RECOVERY + harness],
        input=json.dumps({"responses": responses, "retry": retry, "aborted": aborted}),
        text=True, capture_output=True, timeout=5, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("failure", [500, 502, 503, 504, 429, "network", "timeout", "body_network", "body_timeout"])
def test_transient_status_failure_recovers_without_generation_post(failure) -> None:
    result = _request_status([failure, 200])
    assert result["data"]["panels"][0]["status"] == "not_started"
    assert len(result["calls"]) == 2
    assert result["waits"] == [1000]
    assert all(call == {"url": "/api/projects/fixture-project/generation/status", "method": "GET", "body": None} for call in result["calls"])


def test_persistent_server_failure_stops_after_bounded_status_retries() -> None:
    result = _request_status([503])
    assert len(result["calls"]) == 3
    assert result["waits"] == [1000, 2000]
    assert result["error"] == {"status": 503, "category": "server", "kind": "server"}


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "forbidden"), (404, "not_found"), (422, "response")])
def test_non_transient_status_errors_are_not_automatically_retried(status, kind) -> None:
    result = _request_status([status, 200])
    assert len(result["calls"]) == 1
    assert result["waits"] == []
    assert result["error"]["kind"] == kind


@pytest.mark.parametrize("response", ["invalid", "empty"])
def test_invalid_status_response_is_not_treated_as_network_or_success(response) -> None:
    result = _request_status([response, 200])
    assert len(result["calls"]) == 1
    assert result["error"]["category"] == "invalid_response"
    assert result["error"]["kind"] == "response"


@pytest.mark.parametrize("options", [{"retry": False}, {"aborted": True}])
def test_polling_or_cancelled_read_does_not_multiply_status_retries(options) -> None:
    result = _request_status([503, 200], **options)
    assert len(result["calls"]) == 1
    assert result["waits"] == []
