# Project Intelligence MCP向け観測API

通常の漫画生成とは独立した、運用者専用の認証付きGET API。
Render → 本サービス → Environmentに `MCP_OBSERVER_TOKEN`（専用ランダムSecret、32文字以上）を登録する。
値をコード・チャット・ログへ貼らない。同じSecretを外部MCPサービスにも設定する。
未設定時は503、認証不正は401、GET以外は405。

- `/api/observer/health`
- `/api/observer/projects`
- `/api/observer/generation-jobs`
- `/api/observer/exports`
- `/api/observer/tables/{table}`
- `/api/observer/activity`（entityごとにpagination）

公開列は `app/observability.py` のFIELDSのみ。本文・prompt・Knowledge・画像・error本文・ユーザー・path・任意JSON・Secretを返さない。値もUUID、日時、固定enumへ投影する。
limit最大25、offset最大10000、期間最大7日、既定直近7日。start_at/end_atはISO日時。columns/filters/orderは構造化JSON。Raw SQL入力はない。日時条件はprojects/jobsがupdated_at、exportsがcreated_at。

GETはセッション削除・ジョブ復旧・生成・DB初期化を呼ばない。新しいPythonプロセスで `mode=ro&readonly_shm=1` とquery_only、authorizerを使い、writerと共有メモリの書込mappingを共有しない。ライブDBにimmutableは使用しない。

認証設定済みの通常起動時だけmode=roのkeeper接続を維持する。既存 `db.init_db` に任意のon_readyフックを追加し、初期化接続が閉じる前にread-only keeperを確保する。既存初期化のSQL・トランザクションは変更しない。GETでは初期化や補助ファイル作成を行わず、準備不足なら503を返す。独自VFSやSQLite更新時は無変更テストを再実行する。最大同時読取4、SQL1.5秒、子プロセス4秒の上限がある。

DBファイルを公開せず、Persistent Diskのmount・既存DATABASE_URL・STORY_MANGA_DATA_DIRを変更しない。既存デプロイ設定はそのまま。GitHubへpushする前に自動Deploy設定を確認し、人間が本番Deployする。

イベントは現在状態の観測で、完全な遷移履歴ではない。Runtime LogとDeploymentはMCP側がRender GET APIから取得する。GitHubの変更履歴はChatGPT側GitHub連携を使用する。

検証: `pytest tests/test_observability.py`。全pytestの前に一時データディレクトリ・demo providerを環境変数へ設定し、import時の既存初期化を実データへ向けない。Secretや実データをfixtureにしない。
