# Story to Manga

物語を、読める漫画へ変換する制作ワークスペースです。

## 起動

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

ブラウザで `http://127.0.0.1:8000` を開き、「デモデータで試す」から主要フローを確認できます。

## 環境変数

`.env.example` を参照してください。`OPENAI_API_KEY` が未設定の場合はデモAIとデモアートが動作します。実AIを利用する場合は、サーバー側の環境変数またはプロジェクト直下の`.env`だけにキーを設定し、`AI_PROVIDER=openai` と `IMAGE_PROVIDER=openai` を指定してください。キーはクライアントへ渡しません。

テキスト処理はOpenAI Responses APIのStructured Outputs（JSON Schema）を使い、`OPENAI_TEXT_MODEL`でモデルを変更できます。パネル画像はOpenAI Images APIを使い、`OPENAI_IMAGE_MODEL`で変更できます。既定値はそれぞれ`gpt-5.6-luna`と`gpt-image-2`です。タイムアウト、再試行回数、最大出力トークンも環境変数で制限しています。

## 対応形式

- 直接入力
- `.txt`
- `.md`
- `.pdf`
- `.docx`

アップロード上限は5MBです。本文は50万文字以内で、抽出不能・空ファイルは拒否します。

## 主な画面

- `/login`: ログイン、新規登録、デモログイン
- `/dashboard`: Project一覧
- `/projects/new`: 本文入力・ファイル取り込み
- `/knowledge`: Project横断のKnowledge Library（参照資料、Version履歴、アーカイブ）
- `/settings`: アカウント、AI接続状態、表示テーマ
- `/knowledge/{id}`: Knowledgeの本文プレビュー、メタデータ、Version追加・有効化
- `/projects/{id}`: 物語、Knowledge設定、解析、漫画化設定、キャラクター、ネーム、生成、編集、QA、プレビュー、書き出し

## Knowledge Library

物語の設定資料・画面ルール・キャラクター補助資料などを複数Documentとして登録できます。対応形式は本文入力、`.txt`、`.md`、`.pdf`、`.docx`です。DocumentはVersion履歴を持ち、Projectごとに以下を設定できます。

- 参照の有効/無効
- 優先度
- Story Analysis、Character、Storyboard、Image Generation、Quality Checkなどの参照Scope
- 常に最新Versionを使う、または特定Versionへ固定

Knowledge本文は命令ではなく参照資料として扱い、AI処理で使ったDocument/Version/Chunkを追跡できる形で保存します。Knowledgeを更新しても、既存のコマ画像は自動再生成しません。

## セキュリティ上の注意

- セッションは不透明なランダムトークンをHttpOnly Cookieに保存し、DBにはハッシュだけ保存します。
- Project、生成画像、書き出しファイルは所有者確認後に返します。
- 本文はAIへの命令ではなく参照コンテンツとして扱います。
- Knowledgeも同様に参照コンテンツとして扱い、プロンプト境界を明示します。
- APIキーや本文を通常ログへ出力しません。

## Render

`render.yaml` と `Dockerfile` を用意しています。SQLiteを保持するにはRenderの永続ディスクが必要です。GitHub/Renderアカウントへの接続や公開操作は、所有者の認証が必要なため、ここでは設定ファイルまで準備しています。デモ画像はPNGで保存され、PDFには生成済み画像とアプリ側のセリフを埋め込みます。
