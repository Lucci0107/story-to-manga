# Story to Manga 開発状況

更新日: 2026-09-07

## 現在の状態

ローカルで主要なStory to Manga制作フロー、Project横断Knowledge Library、OpenAI実AI接続とモデル選択・フォールバックを確認できるMVPです。ローカルGitは`main`で初期化済みですが、外部remoteは未接続です。本番デプロイは認証待ちです。

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
- 画像生成PromptへのKnowledge参照境界、生成結果の参照Version/Chunk記録
- 本文、ネーム、Character Bible、画像、Knowledge参照解決を確認するKnowledge-aware QA
- コマの表示方法（全体を表示 / 枠に合わせる）をPreviewとPDFへ反映
- 生成済み画像を埋め込むPDFと、画像/Project JSONのZIP
- アカウント、接続状態、表示テーマを確認する設定画面
- RTL/LTR、ライト/ダークテーマ、モバイル/デスクトップ対応
- Render用`render.yaml`、Dockerfile、環境変数サンプル
- `.env` / `.env.*` のGit除外ルール（`.env.example`は例外）

## 検証済み

- `pytest`: 33 passed（既存26 + AIモデル設定7）
- `node --check static/js/app.js`
- `PYTHONPYCACHEPREFIX=/tmp/... python -m compileall app`
- `pip check`
- APIキー文字列のソース混入チェック（検出なし）
- 実ブラウザで新規登録、KnowledgeのMarkdown/direct入力、Version追加・旧Version有効化、Project紐付け、Scope/優先度/follow-latest/pinned設定を確認
- 実ブラウザで物語抽出、解析、設定、Character Bible編集、ネーム編集、コマ生成・個別再生成、QA、Preview、PDF/ZIP Export、リロード復元を確認
- PNG画像表示、吹き出し/ナレーション/SFX、モバイル390px・デスクトップ1440pxレイアウト、ライト/ダークテーマを確認
- ブラウザconsoleのerror/warningなし
- 実ブラウザでデモモードのコマ生成、個別再生成、リロード後の画像保持を再確認
- 隔離SQLite環境で実AIスモークを確認：`/api/health`のOpenAI切替、Knowledge 1件の参照、Analysis、Character Bible、Storyboard、Knowledge-aware QA、1ページ5コマの生成、1コマのOpenAI画像生成、画像GET、Project再読込後の`completed`/`revision 1`/画像保持
- AIモデル設定画面でプリセット変更、詳細設定展開、工程別選択、保存・再読み込み、Astra可用性表示、390px/1440px表示、コンソール警告なしを確認
- OpenAIモデル一覧APIを低コストに1回確認し、GPT-6 Astraは対象組織で未提供、GPT-5.6 Sol/Terra/Lunaは利用可能と確認
- 実AIの最小Story AnalysisをAstra指定で1回実行し、AstraのアクセスエラーからGPT-5.6 Solへフォールバック、解析結果保存、requested/actual/fallback/reasoningメタデータ、リロード後の実行モデル表示を確認（画像生成は追加実行なし）

## In Progress

- GitHub/Renderの所有者認証を待つ本番デプロイ準備

## Remaining

- GitHub remote接続、Render認証、Production QA
- Render Secret環境変数への`OPENAI_API_KEY`設定と本番AI疎通確認

## Failed

- なし。Docker buildはDocker daemon停止により実行できませんでしたが、Render native buildにはDockerは必須ではありません。

## Blocked

- GitHub repository作成/remote接続、Renderログイン/OAuth、実AI用secret入力は本人操作が必要です。
- `gh auth status`ではGitHubアカウントの保存済みTokenが無効と報告されています。再認証後にremote作成またはRender連携へ進めます。
- Renderダッシュボードはログイン画面で、Render CLI/専用Connectorも利用できません。in-app Browserでの自動公開はここで停止しています。
- ローカル`.env`のOpenAIキーは設定済みで、実AIスモークは成功しました。Astraは現在の組織では未提供のため、実際の分析はGPT-5.6 Solへ安全にフォールバックしました。本番ではRender側のSecretへ同じ用途のキーを登録する必要があります。

## 外部接続待ち

- このアプリ用のGitHub remoteは未作成です。ローカルGitは初期化済みです。
- GitHub/Renderの所有者認証・接続情報もないため、本番デプロイとProduction QAは未実施です。`render.yaml`、Dockerfile、health checkは準備済みです。
- ローカルのキーなし既定値はデモですが、`OPENAI_API_KEY`がある場合はプロバイダ未指定でもOpenAIを選択します。Renderは`AI_PROVIDER=openai` / `IMAGE_PROVIDER=openai`を設定済みで、`OPENAI_API_KEY`だけがSecret待ちです。

## Deployment

- Render native Python build（`pip install -r requirements.txt` → `uvicorn`）を優先する構成です。
- SQLite保存先は`render.yaml`の永続ディスクへ設定済みです。
- ローカル初回コミット: `213096b Build Story to Manga MVP with Knowledge Library`
- AI接続コミット: `9e522f1 Connect OpenAI Responses and image providers`
- 状態・Git安全設定コミット: `8f52105`、`16f135b`
- Production URLは未取得です。
- Renderのnative build設定へResponses API / Images APIのURL、モデル、タイムアウト、再試行、最大出力トークンを追加済みです。APIキーは設定していません。
- ローカル実AIスモークは成功済みですが、Renderの認証・Secret設定・本番URL取得・Production QAは未実施です。

## 再開時の確認

1. `README.md`とこのファイルを読む
2. `STORY_MANGA_DATA_DIR=/tmp/story-manga-check .venv/bin/pytest -q`を実行する
3. `STORY_MANGA_DATA_DIR=/tmp/story-manga-local AI_PROVIDER=demo IMAGE_PROVIDER=demo .venv/bin/uvicorn app.main:app --reload`でブラウザQAを再実行する
4. GitHubリポジトリとRenderの所有者認証が利用可能になったら、Render Secretへキーを設定して本番公開へ進む
5. Production URLでhealth、AI、画像保存、Reload、Preview、Exportを確認する
