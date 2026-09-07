# Story to Manga 開発状況

更新日: 2026-09-07

## 現在の状態

ローカルで主要なStory to Manga制作フロー、Project横断Knowledge Library、OpenAI実AI接続とモデル選択・フォールバックを確認できるMVPです。GitHubの`main`へ接続・pushし、GitHub Actions CI成功まで確認済みです。永続化層はSQLite／ローカルStorageを維持したまま、将来のPostgreSQL／S3互換Storageへ移行できる境界を追加済みです。本番デプロイはRender認証と課金承認待ちです。

```text
本文入力 / ファイル抽出
  -> Knowledge Library（任意の参照資料・Version管理）
  -> 物語解析
  -> 漫画化設定
  -> Character Bible
  -> ネーム編集
  -> コマ画像生成
  -> 個別再生成
  -> セリフ合成
  -> ページPreview
  -> Knowledge-aware QA
  -> PDF / ZIP Export
```

## 完了済み

- FastAPI、Jinja2、SQLiteの単一アプリ構成
- セッション認証、ユーザーごとのProject所有権確認
- txt、md、PDF、docx、直接入力の本文取り込み
- 5MB上限、拡張子/MIME検証、空本文拒否、文字コード対応
- Knowledge Library（複数Document、Markdown見出し保持、正規化、SHA-256重複判定、Bounded Chunk）
- KnowledgeのVersion履歴、旧Versionの再有効化、active/archived管理、Project単位のenable/disable
- Projectごとの優先度、参照Scope、follow-latest / pinned Version設定と、AI処理ごとの参照メタデータ
- 物語解析、漫画化設定、キャラクター、ネームの編集と永続化
- ページ・コマの追加、削除、並び替え、レイアウト変更
- コマ単位の生成Job、状態表示、再試行、重複リクエスト抑止
- デモPNG画像生成と、OpenAI画像APIへのサーバー側接続境界
- OpenAI Responses APIのStructured OutputsによるStory Analysis、Character Bible、Storyboard、Knowledge-aware QA、パネルPrompt生成
- OpenAIモデル能力レジストリ、Auto / Highest Quality / Balanced / Economyプリセット、工程別モデル選択、Global / Project overrideのサーバー側解決
- GPT-6 Astraのモデル一覧APIによる可用性表示（5分キャッシュ）、アクセス不可時のGPT-5.6 Solへの限定フォールバック、requested/actual/fallback/reasoningメタデータ保存
- GPT-6 Astra、GPT-5.6 Sol/Terra/LunaのResponses API選択と、GPT-Image-2の画像専用設定・allowlist分離
- OpenAI Images API（`gpt-image-2`）のbase64/署名URL画像保存、画像形式検証、タイムアウト、限定再試行
- AI出力のJSON Schema要求・サーバー側正規化・不正形式の1回限定修復要求
- `.env`自動読込、`OPENAI_TEXT_MODEL` / `OPENAI_IMAGE_MODEL` / タイムアウト / 再試行回数の中央設定
- デモ入口から実AIを呼ばない課金防止、ユーザー編集Promptの保持、個別パネル再生成
- アプリ側の吹き出し、ナレーション、効果音
- 主要な長時間処理を覆う共通Processing Dialog（中央表示、Backdrop、Spinner、進捗文、操作ブロック、focus復元、失敗時cleanup）
- 画像生成PromptへのKnowledge参照境界、生成結果の参照Version/Chunk記録
- 本文、ネーム、Character Bible、画像、Knowledge参照解決を確認するKnowledge-aware QA
- コマの表示方法（全体を表示 / 枠に合わせる）をPreviewとPDFへ反映
- 生成済み画像を埋め込むPDFと、画像/Project JSONのZIP
- アカウント、接続状態、表示テーマを確認する設定画面
- RTL/LTR、ライト/ダークテーマ、モバイル/デスクトップ対応
- Render用`render.yaml`、Dockerfile、環境変数サンプル、GitHub Actions CI
- RenderのGit連携用`main` branch / `autoDeployTrigger: checksPass`、OpenAI Secretの`sync: false`宣言、公開後Smoke Checkスクリプト
- RenderではFree Web ServiceにPersistent Diskを付けられず、現行のSQLite・画像・Knowledge・Export保存を守るには有料Web Serviceが必要であることを確認
- `render.yaml`のWeb Service planを`0.5c-512mb`（Starter相当）として明示し、1GB Persistent Diskを維持
- `.env` / `.env.*` のGit除外ルール（`.env.example`は例外）
- `DATABASE_URL`でSQLite／PostgreSQL backendを切り替えるDatabase serviceと、DB-API差分を隠すRepository接続境界
- `StorageService`とLocalFileStorageによる画像／Export保存の集約、Storage key保存、旧`file_path`の読み出し互換
- 外部PostgreSQL／Object Storageの実接続・既存データ移行を行わずに、将来の移行設定・依存関係・テスト境界を準備

## 検証済み

- `pytest`: 47 passed（既存テスト + 永続化境界・Storage key回帰確認）
- `psycopg[binary]`を含む依存関係でPostgreSQL接続backendを準備（外部DBへの接続は未実施）
- `node --check static/js/app.js`
- `PYTHONPYCACHEPREFIX=/tmp/... python -m compileall app`
- `pip check`
- 永続化変更後のAPIテストで、画像取得、PDF／ZIP生成・ダウンロード、ExportのStorage key保存を確認
- 永続化境界変更後、隔離SQLite領域で`production_smoke.py`を実行し、health、ログイン画面、CSS、JavaScriptのHTTP到達性を確認（全項目OK）
- APIキー文字列のソース混入チェック（検出なし）
- 実ブラウザで新規登録、KnowledgeのMarkdown/direct入力、Version追加・旧Version有効化、Project紐付け、Scope/優先度/follow-latest/pinned設定を確認
- 実ブラウザで物語抽出、解析、設定、Character Bible編集、ネーム編集、コマ生成・個別再生成、QA、Preview、PDF/ZIP Export、リロード復元を確認
- PNG画像表示、吹き出し/ナレーション/SFX、モバイル390px・デスクトップ1440pxレイアウト、ライト/ダークテーマを確認
- ブラウザconsoleのerror/warningなし
- 実ブラウザでデモモードのコマ生成、個別再生成、リロード後の画像保持を再確認
- 隔離SQLite環境で実AIスモークを確認：`/api/health`のOpenAI切替、Knowledge 1件の参照、Analysis、Character Bible、Storyboard、Knowledge-aware QA、1ページ5コマの生成、1コマのOpenAI画像生成、画像GET、Project再読込後の`completed`/`revision 1`/画像保持
- AIモデル設定画面でプリセット変更、詳細設定展開、工程別選択、保存・再読み込み、Astra可用性表示、390px/1440px表示、コンソール警告なしを確認
- 共通Processing Dialogをデモプロバイダで確認：画像一括生成、個別再生成、物語解析、Character Bible、Storyboard、Knowledge取り込み、Knowledge-aware QA、PDF/ZIP Export、入力エラー。Desktop/Mobile中央配置、進捗文、二重送信抑止、成功/失敗後の自動終了、Reload後の非表示、ライト/ダークテーマ、コンソールエラーなし
- OpenAIモデル一覧APIを低コストに1回確認し、GPT-6 Astraは対象組織で未提供、GPT-5.6 Sol/Terra/Lunaは利用可能と確認
- 実AIの最小Story AnalysisをAstra指定で1回実行し、AstraのアクセスエラーからGPT-5.6 Solへフォールバック、解析結果保存、requested/actual/fallback/reasoningメタデータ、リロード後の実行モデル表示を確認（画像生成は追加実行なし）

## In Progress

- GitHub remote接続、`main` push、GitHub Actions CI成功まで完了。Renderの所有者認証・Git連携・Blueprint作成・Secret設定・課金承認を待つ本番デプロイ準備。

## 永続化移行準備

- 現行の既定値はSQLite＋LocalFileStorageで、既存の`data/`レイアウトとローカル開発フローを維持しています。
- `DATABASE_URL`がPostgreSQLの場合は`psycopg` backendを選択できます。接続失敗時に認証情報をエラーやログへ含めない設計です。
- 画像・Exportの業務処理はStorage keyだけを扱い、Pathと`read_bytes`／`write_bytes`はLocalFileStorageへ閉じ込めています。
- 外部DB／Object Storageへの実移行、既存SQLiteデータのバックフィル、S3互換backendの実装は今回の対象外です。データを失う設定変更は行っていません。

## Remaining

- Render認証、Production QA
- Render Secret環境変数への`OPENAI_API_KEY`設定と本番AI疎通確認
- Render Blueprintの有料Web Service（$7/月）＋Persistent Disk（$0.25/月）の課金承認

## Failed

- なし。Docker buildはDocker daemon停止により実行できませんでしたが、Render native buildにはDockerは必須ではありません。

## Blocked

- Renderログイン/OAuth、GitHub repositoryとのRender連携、実AI用secret入力は本人操作が必要です。
- 現行保存方式を維持した長期本番には有料構成が必要です。Free Web ServiceはPersistent Diskを利用できず、SQLite・画像・Knowledge・Exportが再起動／再デプロイ／スピンダウンで失われるため、`plan: free`への変更は行っていません。
- Blueprint見積もりはWeb Service $7/月 + Persistent Disk $0.25/月 = $7.25/月です。支払い承認なしではDeploy Blueprintへ進めません。
- `gh auth status`ではGitHubアカウントの保存済みTokenが無効と報告されていますが、Gitの認証情報でremote設定と`main` pushは完了しています。Render連携にはRender側の所有者認証が必要です。
- Renderダッシュボードはログイン画面で、Render CLI/専用Connectorも利用できません。in-app Browserでの自動公開はここで停止しています。
- ローカル`.env`のOpenAIキーは設定済みで、実AIスモークは成功しました。Astraは現在の組織では未提供のため、実際の分析はGPT-5.6 Solへ安全にフォールバックしました。本番ではRender側のSecretへ同じ用途のキーを登録する必要があります。

## 外部接続待ち

- GitHub remoteは`https://github.com/Lucci0107/story-to-manga.git`へ設定済みで、ローカル`main`はorigin/mainを追跡しています。GitHub repositoryはPublicの空repositoryから開始しました。
- GitHub Actionsの`Validate application`は`65ba257`でsuccessを確認しました。Renderの所有者認証・連携情報と課金承認がないため、本番デプロイとProduction QAは未実施です。`render.yaml`、Dockerfile、health checkは準備済みです。
- ローカルのキーなし既定値はデモですが、`OPENAI_API_KEY`がある場合はプロバイダ未指定でもOpenAIを選択します。Renderは`AI_PROVIDER=openai` / `IMAGE_PROVIDER=openai`を設定済みで、`OPENAI_API_KEY`だけがSecret待ちです。

## Deployment

- Render native Python build（`pip install -r requirements.txt` → `uvicorn`）を優先する構成です。
- Web Serviceは`plan: 0.5c-512mb`、SQLite保存先は`render.yaml`の1GB永続ディスクへ設定済みです。Free Web Serviceではdiskが使えず、現行の永続化要件を満たせません。
- ローカル初回コミット: `213096b Build Story to Manga MVP with Knowledge Library`
- AI接続コミット: `9e522f1 Connect OpenAI Responses and image providers`
- 状態・Git安全設定コミット: `8f52105`、`16f135b`
- GitHub remote / CI準備コミット: `6b39569`、CI修正`79b763d`
- GitHub Actions CI成功確認: workflow run `34071774041`（`65ba257`）
- Render Free制約調査・有料永続構成明示コミット: `b76e818`
- GitHub Actions CI成功確認: workflow run `34072335458`（`b76e818`）
- GitHub repository: `https://github.com/Lucci0107/story-to-manga`
- Production URLは未取得です（Render課金承認・Blueprint作成待ち）。
- GitHub Actionsの`.github/workflows/ci.yml`は`main` push / PRで依存関係、pytest、compileall、JavaScript構文、pip checkを実行します。RenderはCI成功後のみmainを自動デプロイする設定です。
- `scripts/production_smoke.py`で公開URL、`/api/health`、Login、CSS/JSの到達性をJSONで確認できます。
- Renderのnative build設定へResponses API / Images APIのURL、モデル、タイムアウト、再試行、最大出力トークンを追加済みです。APIキーは設定していません。
- ローカル実AIスモークは成功済みです。Render Free制約の調査、現行有料構成の保持、39件の回帰テスト、ローカルSmoke Checkは完了しました。Renderの認証・課金承認・Secret設定・本番URL取得・Production QAは未実施です。

## 再開時の確認

1. `README.md`とこのファイルを読む
2. `STORY_MANGA_DATA_DIR=/tmp/story-manga-check .venv/bin/pytest -q`を実行する
3. `STORY_MANGA_DATA_DIR=/tmp/story-manga-local AI_PROVIDER=demo IMAGE_PROVIDER=demo .venv/bin/uvicorn app.main:app --reload`でブラウザQAを再実行する
4. GitHubリポジトリとRenderの所有者認証が利用可能になったら、Render Secretへキーを設定して本番公開へ進む
5. Production URLでhealth、AI、画像保存、Reload、Preview、Exportを確認する
