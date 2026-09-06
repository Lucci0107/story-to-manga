# Story to Manga 実装計画

## 1. 既存環境の確認

- 既存のFastAPI/Jinja2/SQLite MVP、`.venv`、Render設定、Dockerfile、テスト基盤を確認し、不要なフレームワーク移行は行わない。
- 既存の認証、Project CRUD、本文抽出、AI/画像Provider境界、PDF/ZIP Exportを維持し、Knowledge LibraryとQAを同じ単一アプリへ統合する。
- ローカルGitを`main`で初期化し、別ProjectのGitHubリポジトリへ混在させない。GitHub remoteとRenderの所有者認証は未接続のため、公開操作は設定確認までとする。
- 永続化は外部サービスを要求しないSQLiteを採用する。Renderでは永続ディスクをマウントする。
- APIキーがない開発環境でも一連の制作工程を検証できるよう、決定論的なデモAI/画像Providerを標準にする。

## 2. アーキテクチャ

```text
Browser
  ├─ Jinja2 HTML + CSS + JavaScript workspace
  └─ JSON API
       ├─ SQLite: users / sessions / projects / generation_jobs / exports
       ├─ Knowledge: documents / versions / chunks / project selections / processing jobs
       ├─ Story extraction: txt / md / PDF / docx
       ├─ AI provider boundary: demo or OpenAI-compatible JSON API
       ├─ Artwork provider boundary: demo PNG now, image API later
       ├─ Knowledge retrieval: scope + priority + follow_latest/pinned + bounded context
       └─ Knowledge-aware QA: persistent quality report and traceable references
  └─ Export: PDF / generated-image ZIP
```

Project内の解析、人物、ネーム、設定、Knowledge選択、QA結果はJSON列または正規化テーブルで編集可能な中間データとして保存する。Knowledge本文はDocument/Version/Chunkへ分け、SHA-256で同一本文の重複Versionを防ぐ。所有権確認はすべてのProject API、Knowledge API、メディア、ダウンロードで行う。

## 3. 実装済みの制作工程

1. セッション認証、アカウント登録、デモログイン
2. Project作成と本文直接入力
3. txt / md / PDF / docx本文抽出、5MB上限、空ファイル拒否、文字コード検証
4. Story Analysis生成と編集
5. Manga Settings保存
6. Character Bible生成と編集
7. Page / Panel構成生成、追加、削除、並び替え、編集
8. コマ単位の生成Job、状態表示、個別再生成、実行中の重複排除
9. アプリ側の吹き出し、ナレーション、SFX合成プレビュー
10. PDF / ページアートZIP書き出しと未生成警告
11. レスポンシブな制作ワークスペース、ライト/ダークテーマ、主要loading/error/empty/success状態
12. アカウント、接続状態、表示テーマを確認する設定画面

## 4. Knowledge Libraryの設計

- Libraryは複数Documentを持ち、各Documentは削除せずVersion履歴を追加する。内容Hashが一致する再アップロードは新しいVersionを作らない。
- `txt`、`md`、`pdf`、`docx`、直接入力を受け付け、Markdown見出しを保持して正規化・Bounded Chunk化する。本文は上限まで読み込み、後半を無言で切り捨てない。
- ProjectはDocumentごとにenabled、priority、scope、follow-latest/pinnedを保存する。Archived Documentは新規選択できず、既存参照の自動解決から除外する。
- Story Analysis、Character、Storyboard、Image Generation、Quality Checkごとに同じProject設定から参照を解決し、Version/Chunk/見出しを出力へ記録する。
- Knowledge更新は既存Artworkを自動再生成しない。必要な場合だけユーザーが個別再生成を開始する。

## 5. 本番接続時の切り替え

- `AI_PROVIDER=openai` と `OPENAI_API_KEY` をサーバー環境変数に設定すると、解析、人物、ネームの外部LLM接続境界を利用できる。
- 画像APIは `app/services/artwork.py` の `save_panel_artwork` 境界へ接続済み。`IMAGE_PROVIDER=openai` のときだけサーバー側でbase64画像を受け取り、返却された画像パスとrevisionを保存する。デモプロバイダでもPNGを保存し、PDFは保存済み画像へ吹き出し等を合成する。
- 本番で複数インスタンスを動かす場合は、BackgroundTasksをキュー基盤へ移し、SQLiteをPostgres等へ移行する。
- 大きな作品を扱う場合は、物語本文にもKnowledgeと同様の段階的なChunk/階層要約を適用する。
- 本番のAI/画像秘密鍵はRenderのSecret環境変数へ設定し、クライアントへ公開しない。

## 6. 品質ゲート

- `pytest -q`
- `node --check static/js/app.js`
- `python -m compileall app`
- `uvicorn app.main:app` 起動後、ブラウザでデモログイン、解析、編集、生成、プレビュー、PDF/ZIPを確認
- KnowledgeのVersion/Scope/参照トレース、QA、アーカイブ、失敗時retryもAPIテストと実ブラウザで確認済み。
- Render用 `render.yaml` と `Dockerfile` を確認済み。外部アカウント認証なしでは本番公開操作は行わない。
