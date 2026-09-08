# Story to Manga 開発状況

更新日: 2026-09-08

## 現在の状態

ローカルで主要なStory to Manga制作フロー、Project横断Knowledge Library、OpenAI実AI接続とモデル選択・フォールバックを確認できるMVPです。GitHubの`main`へ接続・pushし、GitHub Actions CI成功後にRenderへ自動デプロイできる状態を確認済みです。永続化層はSQLite／ローカルStorageを維持したまま、将来のPostgreSQL／S3互換Storageへ移行できる境界を追加済みです。本番URLでSmoke Check、低負荷Production QA、管理者認証確認まで完了しています。言語・読順ロック機能と、Story Analysis由来のAI漫画化設定推奨も実装・ローカル・本番検証済みです。2026-09-08の全体最適化レビューでは、既存構成を維持しながら共有Demoの本番無効化、再起動時Job復旧、同時生成のDB一意保証、Project資産削除、抽出上限、security header、Jinja2 security patchをローカル検証済みです。

**最終状態: COMPLETE**（将来の外部PostgreSQL／Object Storage移行は別タスク）

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
- 外部AIの長時間Storyboardを永続Jobへ登録し、HTTPタイムアウトを避けるバックグラウンド処理とUIポーリング
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
- Story to Mangaブランドのfavicon（SVG、ICO、16/32px PNG、180px Apple Touch Icon）と全base templateへの参照
- `users.role`の後方互換migration、`ADMIN_EMAIL` / `ADMIN_INITIAL_PASSWORD`によるidempotentなserver-side admin bootstrap
- 既存ユーザーのパスワードを上書きしないadmin promotion、admin-only `/api/admin/status`、一般ユーザーへの403保護
- 本番管理者のログイン、ログアウト、再ログイン、認証済みadmin status確認
- RTL/LTR、ライト/ダークテーマ、モバイル/デスクトップ対応
- Render用`render.yaml`、Dockerfile、環境変数サンプル、GitHub Actions CI
- RenderのGit連携用`main` branch / `autoDeployTrigger: checksPass`、OpenAI Secretの`sync: false`宣言、公開後Smoke Checkスクリプト
- RenderではFree Web ServiceにPersistent Diskを付けられず、現行のSQLite・画像・Knowledge・Export保存を守るには有料Web Serviceが必要であることを確認
- `render.yaml`のWeb Service planを`0.5c-512mb`（Starter相当）として明示し、1GB Persistent Diskを維持
- `.env` / `.env.*` のGit除外ルール（`.env.example`は例外）
- `DATABASE_URL`でSQLite／PostgreSQL backendを切り替えるDatabase serviceと、DB-API差分を隠すRepository接続境界
- `StorageService`とLocalFileStorageによる画像／Export保存の集約、Storage key保存、旧`file_path`の読み出し互換
- 外部PostgreSQL／Object Storageの実接続・既存データ移行を行わずに、将来の移行設定・依存関係・テスト境界を準備
- Projectごとの漫画言語（日本語／English）と、言語から自動決定するserver-sideの読み方向
- Story Analysisを主sourceにした漫画化設定のAI推奨（ページ数、スタイル、色、テンポ、セリフ量、想定読者、理由、シーン別ページ配分）
- `settings_recommendation`用Structured Outputs、Analysis複雑度ベースの決定論的fallback、Knowledge adaptation Scope参照
- 推奨値の永続化、Analysis fingerprintによるstale検出、user override保護、再提案のpreview/apply/cancel
- 言語別のPanel.order、グリッド視覚配置、吹き出し・ナレーション・SFX順、Previewナビゲーション、PDFコマ配置
- Storyboard Structured Output、Panel Prompt、Knowledge-aware QAへProjectの言語・読順ルールを注入し、Knowledgeの逆方向指定を上書き
- 旧`rtl`/`ltr`設定と0始まりのPanel orderを壊さずに移行する冪等な正規化、言語変更時の画像・本文保持

## 検証済み

- `pytest`: 73 passed（既存68件 + 本番Demo無効化、Job復旧、Storage cleanup、docx展開上限等の回帰確認）
- `psycopg[binary]`を含む依存関係でPostgreSQL接続backendを準備（外部DBへの接続は未実施）
- `node --check static/js/app.js`
- `PYTHONPYCACHEPREFIX=/tmp/... python -m compileall app`
- `pip check`
- 全体最適化レビュー後の隔離production設定HTTP smokeでhealth/login/CSS/JavaScript 200、`/demo` 404、HSTS/no-storeを確認
- 追跡ソースのsecret scanでactual secret検出なし
- 永続化変更後のAPIテストで、画像取得、PDF／ZIP生成・ダウンロード、ExportのStorage key保存を確認
- 永続化境界変更後、隔離SQLite領域で`production_smoke.py`を実行し、health、ログイン画面、CSS、JavaScriptのHTTP到達性を確認（全項目OK）
- APIキー文字列のソース混入チェック（検出なし）
- 実ブラウザで新規登録、KnowledgeのMarkdown/direct入力、Version追加・旧Version有効化、Project紐付け、Scope/優先度/follow-latest/pinned設定を確認
- 実ブラウザで物語抽出、解析、設定、Character Bible編集、ネーム編集、コマ生成・個別再生成、QA、Preview、PDF/ZIP Export、リロード復元を確認
- PNG画像表示、吹き出し/ナレーション/SFX、モバイル390px・デスクトップ1440pxレイアウト、ライト/ダークテーマを確認
- ブラウザconsoleのerror/warningなし
- Login、Dashboard、Project、404のbase templateでfavicon各形式を確認し、静的ファイルHTTP 200と16/32/180pxサイズを検証
- 管理者bootstrapの新規作成・重複抑止・既存ユーザー保持、PBKDF2ハッシュ、admin-only endpoint、一般ユーザー403、未設定環境の安全な起動をテスト
- 初回管理者作成後に`ADMIN_INITIAL_PASSWORD`と`ADMIN_EMAIL`を削除しても既存adminを保持する回帰テスト
- 実ブラウザでデモモードのコマ生成、個別再生成、リロード後の画像保持を再確認
- 隔離SQLite環境で実AIスモークを確認：`/api/health`のOpenAI切替、Knowledge 1件の参照、Analysis、Character Bible、Storyboard、Knowledge-aware QA、1ページ5コマの生成、1コマのOpenAI画像生成、画像GET、Project再読込後の`completed`/`revision 1`/画像保持
- AIモデル設定画面でプリセット変更、詳細設定展開、工程別選択、保存・再読み込み、Astra可用性表示、390px/1440px表示、コンソール警告なしを確認
- 共通Processing Dialogをデモプロバイダで確認：画像一括生成、個別再生成、物語解析、Character Bible、Storyboard、Knowledge取り込み、Knowledge-aware QA、PDF/ZIP Export、入力エラー。Desktop/Mobile中央配置、進捗文、二重送信抑止、成功/失敗後の自動終了、Reload後の非表示、ライト/ダークテーマ、コンソールエラーなし
- OpenAIモデル一覧APIを低コストに1回確認し、GPT-6 Astraは対象組織で未提供、GPT-5.6 Sol/Terra/Lunaは利用可能と確認
- 実AIの最小Story AnalysisをAstra指定で1回実行し、AstraのアクセスエラーからGPT-5.6 Solへフォールバック、解析結果保存、requested/actual/fallback/reasoningメタデータ、リロード後の実行モデル表示を確認（画像生成は追加実行なし）
- 本番Smoke Check: `https://story-to-manga-b6bb.onrender.com`、health / login / CSS / JavaScriptが全項目HTTP 200
- 本番Production QA: 合成ProjectでKnowledge upload/selection、Story Analysis、Character Bible、Storyboard Job、Knowledge-aware QA、リロード復元、Preview、PDF/ZIP Export、AIモデル設定の秘密非公開を確認
- 本番実AI画像生成: `gpt-image-2`で先頭コマを1枚だけ生成し、`completed` / `revision 1` / 画像取得 / リロード後の保持を確認（再生成なし）
- 本番デプロイ後QA: login HTMLのfavicon 5参照、favicon各形式HTTP 200、health 200、匿名admin API 401、秘密名/キー非露出、Productionの390px・Dark theme・console errorなしを確認
- 本番管理者QA: 管理者ログイン、ログアウト、再ログイン、ダッシュボード表示、認証済み`/api/admin/status` 200を確認（認証Cookieは読み出し・記録していません）
- 言語・読順ローカルQA: 日本語の右→左（右列先頭）、Englishの左→右（左列先頭）、1始まりのPanel order、吹き出し順、Previewのページ送り、設定保存・Reload、言語変更時の非翻訳・非再生成、Light/Dark、Desktopを確認
- 言語・読順回帰検証: `pytest` 62 passed、Python compileall、JavaScript構文、`pip check`、`git diff --check`を確認
- 言語・読順Production QA: 本番デモProjectでEnglish保存・Reload・`ltr`・Panel 1左列／Panel 2右列、続けて日本語保存・`rtl`・Panel 1右列／Panel 2左列／先頭吹き出し右側を確認。Previewページ送り表示、PDF/ZIP download link、成功後Processing Dialogのhidden cleanupも確認
- 言語・読順Production smoke: `/api/health` 200、login/CSS/JavaScript 200、本番静的ファイルへ言語セレクタ・読順属性・Preview navigation・bubble sideを確認。今回のQAでは実AI／画像生成の追加実行なし
- AI漫画化設定推奨のローカルBrowser QA: 短編Analysis後に中央Processing Dialog、推奨ページ数・理由・シーン別配分を確認。手動設定の保存・Reload保持、再提案のpreview、cancel、apply、言語による右→左表示を確認。実AIテキスト呼び出しは初回推奨と再提案の各1回、画像生成は追加実行なし
- AI漫画化設定推奨の回帰検証: 短編・標準・複雑シナリオでページ数が増加すること、固定40ページでないこと、Structured Output検証、Knowledge参照、AI失敗fallback、stale/user override保護、`settings_recommendation_model`のプリセット解決を確認
- AI漫画化設定推奨のProduction QA: 本番の既存合成Projectで初回推奨値・理由・シーン別配分・Processing Dialogを確認。再提案のProcessing Dialog、プレビュー、キャンセル、再読み込み後の8ページ／日本語RTL保持、推奨ボタンの完了後有効化、本番console error 0件を確認。画像生成は追加実行なし
- 最新修正コミット`b889fe6`のGitHub Actions CI（run `34179819425`）がsuccess。CI通過後のRender自動デプロイを本番ページの修正反映（再提案ボタン有効化）で確認し、`/api/health`はHTTP 200、OpenAI provider表示も確認
- 最終管理者確認コミット`43aa035`のGitHub Actions CI（run `34121680039`）とRender自動deploy（`dep-dafaq6eq1p3s73dofjj0`）がsuccess。直後の一時502回復後、最終Production smokeのhealth/login/CSS/JavaScriptが全項目HTTP 200
- 本番検証用Projectは合成データのため削除せず保持しています。ユーザー操作なしの本番データ削除は行っていません。

## In Progress

- 全体最適化レビューのローカル検証は完了。GitHub CI、Render自動deploy、Production Browser/Smoke QAを継続中です。

## 永続化移行準備

- 現行の既定値はSQLite＋LocalFileStorageで、既存の`data/`レイアウトとローカル開発フローを維持しています。
- `DATABASE_URL`がPostgreSQLの場合は`psycopg` backendを選択できます。接続失敗時に認証情報をエラーやログへ含めない設計です。
- 画像・Exportの業務処理はStorage keyだけを扱い、Pathと`read_bytes`／`write_bytes`はLocalFileStorageへ閉じ込めています。
- 外部DB／Object Storageへの実移行、既存SQLiteデータのバックフィル、S3互換backendの実装は今回の対象外です。データを失う設定変更は行っていません。

## Admin Account Investigation

- 分類: **E（環境変数bootstrap方式）**。管理者機能とbootstrapは実装済みで、Render Secret設定後の最新deployへ反映されています。実アカウントの存在は、資格情報を取得・表示せずに本人がログインした後の`/api/admin/status` 200で最終確認します。
- ローカルSQLiteの既存ユーザーは`demo@example.com`のみで、roleは`user`です。既存ユーザーを自動昇格させず、パスワードも変更していません。Render本番の管理者メールアドレス・初期パスワードはSecret値のため読み出し・記録していません。
- `users.role`は既存DBへ`DEFAULT 'user'`でmigrationされます。`ADMIN_EMAIL`と`ADMIN_INITIAL_PASSWORD`の両方が有効な場合だけ、起動時に新規adminを1件作成します。既存adminは保持し、同じメールの既存ユーザーを明示指定した場合はパスワードを上書きせずroleだけをadminへ更新します。
- ログインURLは`/login`で一般ユーザーと共通です。server-sideでadmin roleを検証する`/api/admin/status`を追加し、未認証は401、一般ユーザーは403です。
- ローカル`.env`には設定していません。Renderの`ADMIN_EMAIL` / `ADMIN_INITIAL_PASSWORD` Secretはユーザー設定済みです。設定後のRender deploy `dep-dafaej740ujc73adsi70`（GitHub deployment `6308205978`）と、状態記録更新後の自動deployをsuccess確認しました。実値は記録・表示していません。

## Remaining

- `ADMIN_INITIAL_PASSWORD`（必要に応じて`ADMIN_EMAIL`も）のRender Secret削除は、ユーザーが任意で実施できる後処理です。削除手順はREADMEに記録しています。
- 外部PostgreSQL／Object Storageへの実移行は将来作業として未実施です。

## Failed

- なし。Docker buildはDocker daemon停止により実行できませんでしたが、Render native buildにはDockerは必須ではありません。

## Blocked

- Renderの有料Web Service + Persistent Diskはユーザーが承認・作成済みで、`OPENAI_API_KEY`と管理者用SecretはRender Secretとして設定済みです。実管理者ログイン、ログアウト、再ログイン、認証済みadmin status 200を確認済みです。
- 現行保存方式を維持した長期本番には有料構成が必要です。Free Web ServiceはPersistent Diskを利用できず、SQLite・画像・Knowledge・Exportが再起動／再デプロイ／スピンダウンで失われるため、`plan: free`への変更は行っていません。
- Astraは現在の組織で利用できない場合にGPT-5.6 Solへフォールバックする設計です。秘密値は記録していません。

## 外部接続

- GitHub remoteは`https://github.com/Lucci0107/story-to-manga.git`へ設定済みで、ローカル`main`はorigin/mainを追跡しています。GitHub repositoryはPublicの空repositoryから開始しました。
- GitHub Actionsの`Validate application`は`c33da98`でsuccessを確認しました。Render Blueprintは`main`を`autoDeployTrigger: checksPass`で監視し、CI成功後に自動デプロイします。
- RenderのProduction URL、OpenAI Secret、永続ディスクを確認済みです。秘密値はリポジトリ・ログ・ブラウザへ保存していません。

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
- Production URL: `https://story-to-manga-b6bb.onrender.com`
- Render deployment: `dep-daf70mc9v7es73bnvjug` / GitHub deployment `6304526998`（success）
- GitHub Actionsの`.github/workflows/ci.yml`は`main` push / PRで依存関係、pytest、compileall、JavaScript構文、pip checkを実行します。RenderはCI成功後のみmainを自動デプロイする設定です。
- `scripts/production_smoke.py`で公開URL、`/api/health`、Login、CSS/JSの到達性をJSONで確認できます。
- Renderのnative build設定へResponses API / Images APIのURL、モデル、タイムアウト、再試行、最大出力トークンを追加済みです。APIキーは`sync: false`のRender Secretです。
- Storyboard JobのRenderタイムアウト対策コミット: `c33da98`
- GitHub Actions CI成功確認: workflow run `34098711404`（`c33da98`）
- Production QAは1ページ・1コマ画像の低コスト条件で完了しました。Storyboard処理中にRenderの一時502が発生した場合も、Job状態を保持したままUI/QA側で安全に再取得できることを確認しています。
- Favicon/Admin hardeningコミット: `20bdc0a`、favicon MIME portability修正コミット: `3ec23c6`
- GitHub Actions CI成功確認: workflow run `34118066143`（`3ec23c6`）
- Render自動デプロイ成功: `dep-dafa6tvavr4c73bs9ba0`（GitHub deployment `6307945250`）
- Production URL: `https://story-to-manga-b6bb.onrender.com`
- Render Secret設定後の自動デプロイ成功: `dep-dafaej740ujc73adsi70`（GitHub deployment `6308205978`）。状態記録更新コミット後のCI/自動deployもsuccess。最新Production smokeはhealth/login/CSS/JS 200、匿名`/api/admin/status` 401。
- 言語・読順変更コミット: `21a9297`
- 言語・読順変更CI: workflow run `34176030782`（success）
- 言語・読順変更Render deployment: GitHub deployment `6318749467`（success）、environment URL `https://story-to-manga-b6bb.onrender.com`

## 次回セッションの確認

1. `git status --short --branch`で作業ツリーと`origin/main`を確認する
2. 通常のデプロイは`main`へpushし、GitHub Actions成功後のRender自動デプロイを確認する
3. 本番変更がある場合だけ、影響範囲のpytestと`.venv/bin/python scripts/production_smoke.py --url https://story-to-manga-b6bb.onrender.com`を実行する
4. 外部PostgreSQL／S3互換Storageへの移行は、別タスクとしてデータバックフィル計画を作成してから行う
