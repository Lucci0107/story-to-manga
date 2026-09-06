# Story to Manga 開発状況

更新日: 2026-09-07

## 現在の状態

ローカルで主要なStory to Manga制作フローと、Project横断で再利用できるKnowledge Libraryを実行できるMVPです。ローカルGitは`main`で初期化済みですが、外部remoteは未接続です。

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
- アプリ側の吹き出し、ナレーション、効果音
- 画像生成PromptへのKnowledge参照境界、生成結果の参照Version/Chunk記録
- 本文、ネーム、Character Bible、画像、Knowledge参照解決を確認するKnowledge-aware QA
- コマの表示方法（全体を表示 / 枠に合わせる）をPreviewとPDFへ反映
- 生成済み画像を埋め込むPDFと、画像/Project JSONのZIP
- アカウント、接続状態、表示テーマを確認する設定画面
- RTL/LTR、ライト/ダークテーマ、モバイル/デスクトップ対応
- Render用`render.yaml`、Dockerfile、環境変数サンプル

## 検証済み

- `pytest`: 20 passed
- `node --check static/js/app.js`
- `PYTHONPYCACHEPREFIX=/tmp/... python -m compileall app`
- `pip check`
- 実ブラウザで新規登録、KnowledgeのMarkdown/direct入力、Version追加・旧Version有効化、Project紐付け、Scope/優先度/follow-latest/pinned設定を確認
- 実ブラウザで物語抽出、解析、設定、Character Bible編集、ネーム編集、コマ生成・個別再生成、QA、Preview、PDF/ZIP Export、リロード復元を確認
- PNG画像表示、吹き出し/ナレーション/SFX、モバイル390px・デスクトップ1440pxレイアウト、ライト/ダークテーマを確認
- ブラウザconsoleのerror/warningなし

## In Progress

- なし。実装・ローカル検証・デプロイ設定確認まで完了しています。

## Remaining

- GitHub remote接続、Render認証、Production QA

## Failed

- なし。Docker buildはDocker daemon停止により実行できませんでしたが、Render native buildにはDockerは必須ではありません。

## Blocked

- GitHub repository作成/remote接続、Renderログイン/OAuth、実AI用secret入力は本人操作が必要です。
- `gh auth status`ではGitHubアカウントの保存済みTokenが無効と報告されています。再認証後にremote作成またはRender連携へ進めます。

## 外部接続待ち

- このアプリ用のGitHub remoteは未作成です。ローカルGitは初期化済みです。
- GitHub/Renderの所有者認証・接続情報もないため、本番デプロイとProduction QAは未実施です。`render.yaml`、Dockerfile、health checkは準備済みです。
- `AI_PROVIDER=demo`、`IMAGE_PROVIDER=demo`が既定値です。実サービスを使う場合はサーバー環境変数へ秘密鍵を設定します。

## Deployment

- Render native Python build（`pip install -r requirements.txt` → `uvicorn`）を優先する構成です。
- SQLite保存先は`render.yaml`の永続ディスクへ設定済みです。
- ローカル初回コミット: `213096b Build Story to Manga MVP with Knowledge Library`
- Production URLは未取得です。

## 再開時の確認

1. `README.md`とこのファイルを読む
2. `STORY_MANGA_DATA_DIR=/tmp/story-manga-check .venv/bin/pytest -q`を実行する
3. `STORY_MANGA_DATA_DIR=/tmp/story-manga-local AI_PROVIDER=demo IMAGE_PROVIDER=demo .venv/bin/uvicorn app.main:app --reload`でブラウザQAを再実行する
4. GitHubリポジトリとRenderの所有者認証が利用可能になった場合のみ、本番公開とProduction QAへ進む
