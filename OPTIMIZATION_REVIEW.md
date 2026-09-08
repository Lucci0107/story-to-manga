# Story to Manga Optimization Review

更新日: 2026-09-08

## Current Architecture

- FastAPI + Jinja2 + Vanilla JavaScriptの単一Web application。
- Repository層は`DATABASE_URL`でSQLite/PostgreSQLを選択し、StorageServiceは現在LocalFileStorageを利用する。
- AI処理は共通Provider、Model Capability Registry、Structured Outputs、Knowledge Context Resolverを経由する。
- Projectの言語・読順はserver-sideのreading order serviceを正とし、Preview・Export・QAへ共有する。
- GitHub Actionsのcheck成功後だけRenderが`main`を自動デプロイし、SQLiteと生成物はPersistent Diskへ保存する。

## Confirmed Strengths

- Story/Knowledgeを命令ではなく参照内容として区切り、Knowledge送信量を4 Chunk・6,000文字に制限している。
- OpenAIの再試行、Astra fallback、モデルallowlist、requested/actual metadata、画像の個別再生成が中央化されている。
- Project所有権、admin role、HttpOnly/SameSite/Secure Cookie、Storage key検証、Upload形式・MIME・サイズ検証がserver-sideにある。
- 日本語RTL／English LTR、Panel/Bubble order、Manga Settings recommendation、user override保護はテストで一貫している。
- SQLite固有処理とLocal filesystem処理はそれぞれDatabase/Storage境界内に留まる。

## Issues Found

### Critical

- Productionの`/demo`が匿名利用者を同じDemo userへログインさせていた。Project内容が利用者間で共有され、後続操作でproduction AI costを発生させ得る。

### High Value

- Render再起動時にBackgroundTasksの`queued`/`processing` JobとPanelが残り、UIが処理中のまま復旧できない可能性があった。
- 有料生成Jobの重複防止が事前SELECTだけで、同時リクエスト時のDB一意性保証がなかった。
- Project削除後も画像とExportがStorageへ残り、Persistent Diskを継続消費した。
- 圧縮されたdocxの展開後サイズに上限がなく、過大展開でメモリを消費する余地があった。PDFも抽出途中の上限判定がなかった。
- Session失効削除とExport参照に対応する索引がなく、データ増加時に不要な全走査が起き得た。
- 認証済みHTML/APIに明示的な`no-store`がなく、production HSTSもアプリ側では付与していなかった。
- Jinja2 3.1.5はsandboxの`|attr` filterを迂回できる既知問題の修正前で、互換性を保つ3.1.6へ更新可能だった。
- 390pxの実ブラウザ表示でglobal sidebarのテーマボタンが独立した行へ落ち、ラベルが約36px幅で縦に潰れていた。

### Optional

- DashboardはProjectの大きなJSONを一覧時にも読み込む。現状の想定規模では実害を測定できず、API互換性を変える一覧専用DTO化は見送った。
- Login rate limitingと完全なCSRF token方式は将来候補。現状はSameSite CookieとJSON mutation APIで主要なcross-site mutationを抑制しており、分散環境に不完全なin-memory limiterは追加しなかった。
- 長文Storyの階層骨子は全Chunkの冒頭・末尾を利用するが、中間要約のためにAI callを増やす変更は費用対効果が未確認のため見送った。

## Changes Implemented

- `ENABLE_DEMO_LOGIN`を中央設定へ追加し、productionでは明示設定がない限りDemo導線とendpointを無効化。
- 起動時に中断Jobと対象Panelを`failed`へ収束させ、ユーザーが再試行できる状態へ復旧。
- active Jobへ部分一意索引を追加し、競合時は既存Jobを返すようRepositoryを強化。
- StorageServiceへProject単位の削除契約を追加し、Local assets/exportsを所有Projectだけ削除。
- PDF/docxの抽出後上限とdocx展開前のサイズ検証を追加。
- Session/Export索引、production HSTS、認証済み画面/APIの`Cache-Control: no-store`を追加。
- Jinja2だけを3.1.5からsecurity patch releaseの3.1.6へ更新。その他の依存関係は根拠のない一括更新をしていない。
- モバイルglobal sidebarのテーマ操作をheader右端へ固定し、視覚上は36px icon button、アクセシブル名は従来どおり保持するCSSへ修正。CSS cache keyを更新。

## Changes Intentionally Not Implemented

- Framework、AI Provider、Knowledge Library、reading-order engine、database/storage architecture、Render planは変更していない。
- PostgreSQL/S3への実移行、production data migration、画像再生成、モデルrouting変更、依存関係の一括更新は行っていない。
- CSPはPreviewの動的な位置styleに影響するため、十分なnonce/style移行なしには導入していない。

## Validation

- 変更前baseline: `pytest 68 passed`。
- 変更後targeted tests: `40 passed`。
- 変更後full regression: `74 passed`。
- Python compileall、JavaScript syntax、`pip check`、`git diff --check`: success。
- 追跡ソースのsecret scan: actual secret検出なし（`.env.example`の空欄とテスト用検査文字列のみ）。
- 本番設定相当の隔離SQLite HTTP smoke: health/login/CSS/JavaScript 200、`/demo` 404、HSTS/no-storeを確認。
- Browser QA、GitHub CI、Render deploy、Production QAは最終デプロイ確認後に追記する。

## Production State

- Review開始時: commit `bc0c715`、Git working tree clean、production health HTTP 200。
- Render Persistent Diskは公式仕様どおり単一service instanceに限定され、zero-downtime deploy不可。現行SQLite/Local Storageではデータ耐久性のため維持する。
- 最終commit、GitHub CI、Render deployment、Production QAはデプロイ完了後に追記する。
