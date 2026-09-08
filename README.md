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
- `/api/admin/status`: 管理者セッションだけが利用できるserver-side権限確認API
- `/knowledge/{id}`: Knowledgeの本文プレビュー、メタデータ、Version追加・有効化
- `/projects/{id}`: 物語、Knowledge設定、解析、漫画化設定、キャラクター、ネーム、生成、編集、QA、プレビュー、書き出し

## 漫画言語と読順

Projectの漫画化設定で、出力言語を`日本語（ja）`または`English（en）`から選択できます。読み方向は言語からサーバー側で自動決定され、ユーザーが矛盾した方向を保存することはできません。

- `ja`：右から左（右上のコマを先に読む）
- `en`：左から右（左上のコマを先に読む）
- `Panel.order`はDB登録順ではなく、読者の論理読順を表す1始まりの値
- Preview、キーボードページ送り、吹き出し・ナレーション・SFX、PDFのコマ配置も同じ規則を使う
- Storyboard、Panel Prompt、Knowledge-aware QAにはProjectの言語・読順をサーバー確定値として渡す
- Knowledge本文が逆方向を指定しても、現在のProject言語を優先する

言語変更時は既存の画像やセリフ本文を自動翻訳・再生成せず、読順と表示配置だけを新しい規則へ切り替えます。画面上の警告を確認してから保存してください。既存Projectに言語がない場合は、保存済みの旧`rtl`/`ltr`から安全に推定し、移行処理は冪等に実行されます。

## 管理者アカウント

一般ユーザーと管理者は同じ`/login`画面からログインします。管理者アカウントを新規環境で作る場合は、サーバー側のSecretとして`ADMIN_EMAIL`と`ADMIN_INITIAL_PASSWORD`（12文字以上）を両方設定して起動してください。初回起動時だけidempotentに作成され、既存の管理者は変更されません。設定がない場合もアプリは起動し、管理者アカウントは作成されません。

既存ユーザーと同じメールアドレスを`ADMIN_EMAIL`へ明示した場合は、そのユーザーのパスワードを上書きせずadminロールだけを付与します。パスワード値はDB、HTML、JavaScript、ログへ保存・出力しません。`ADMIN_INITIAL_PASSWORD`は`.env`またはRender Secretへ設定し、Gitへコミットしないでください。

初回管理者のログインと`/api/admin/status`の200を確認した後は、Render Dashboardの対象Web Serviceで **Environment** を開き、`ADMIN_INITIAL_PASSWORD`を削除して保存できます。既存のPersistent Disk上のadminレコードとハッシュは変更されないため、通常の再デプロイ後もログインできます。bootstrapを完全に無効化する場合は`ADMIN_EMAIL`も削除できます。DBやPersistent Diskを新規化・消去した場合は自動復旧できないため、復旧用に新しい一時Secretを設定して再デプロイし、管理者作成を確認した後に再び削除してください。Secret削除後もRenderの`sync: false`宣言は残し、実値はGitへ保存しません。

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

## 永続化層と将来の外部サービス移行

DB接続先は`DATABASE_URL`で切り替えられます。未指定時は従来どおり`STORY_MANGA_DATA_DIR`配下のSQLiteを使い、`sqlite:///...`を明示してもSQLiteを選べます。`postgresql://...`または`postgres://...`を指定すると、Repository層がPostgreSQL接続を選択します。PostgreSQL用依存関係とスキーマ適用の境界は準備済みですが、既存SQLiteデータの移行を自動では行いません。実移行時にはバックアップ、データ移行、整合性確認を別途実施します。

生成画像とExportは`StorageService`へ集約し、現在は`STORAGE_BACKEND=local`の`data/assets`・`data/exports`を使います。DBにはローカル絶対パスではなくStorage keyを保存し、既存Exportの`file_path`も読み出し時だけ互換参照します。そのため、将来S3互換Object Storage実装を追加して同じStorage契約へ切り替えられます。`STORAGE_BUCKET`、`STORAGE_ENDPOINT_URL`、`STORAGE_REGION`などの設定名は予約済みですが、今回の変更では外部Storageへ接続しません。`STORAGE_BACKEND`に未実装の値を指定した場合は、ローカルへ黙って保存せず設定エラーにします。

## Render

`render.yaml` と `Dockerfile` を用意しています。GitHubの`main`へpushすると、`.github/workflows/ci.yml`が依存関係、テスト、Python compile、JavaScript構文、`pip check`を検証します。Renderは`autoDeployTrigger: checksPass`により、CI成功後だけ同じ`main`の変更を自動デプロイします。

Production URL: https://story-to-manga-b6bb.onrender.com

このアプリはSQLite、生成画像、Knowledge本文、Exportファイルをローカルファイルシステムへ保存します。RenderのFree Web Serviceはローカルファイルが再起動・再デプロイ・スピンダウンで失われ、Persistent DiskもFreeでは利用できないため、現行の長期本番要件を満たしません。`render.yaml`では保存データを守るため、`plan: 0.5c-512mb`（Starter相当）と1GBのPersistent Diskを明示しています。Freeへ変更する場合は、diskを外して`plan: free`にできますが、同一インスタンスが動作している間だけの検証・デモ用途に限られ、Project・Session・Knowledge・画像・Exportの継続利用は保証できません。

Free Render Postgresへ移行する案もありますが、Freeデータベースは30日で期限切れになるため、長期本番の無課金解決にはなりません。SQLiteから外部データベースへ移行し、画像・Exportを別の耐久ストレージへ移す場合は、別サービスの認証情報と追加実装が必要です。詳細は[Render Freeの制約](https://render.com/docs/free)と[Persistent Diskの仕様](https://render.com/docs/disks)を参照してください。

Render Blueprintで設定する環境変数は、`render.yaml`に秘密値を置かず、次の名前だけを管理します。

- `APP_ENV=production`
- `AI_PROVIDER=openai`
- `IMAGE_PROVIDER=openai`
- `OPENAI_API_KEY`（Render Secretとして登録）
- `OPENAI_RESPONSES_URL`
- `OPENAI_MODELS_URL`
- `OPENAI_IMAGE_URL`
- `OPENAI_TEXT_MODEL`
- `OPENAI_IMAGE_MODEL`
- `OPENAI_TIMEOUT_SECONDS`
- `OPENAI_MAX_RETRIES`
- `OPENAI_MAX_OUTPUT_TOKENS`
- `MAX_UPLOAD_BYTES`
- `SESSION_DAYS`
- `ADMIN_EMAIL`（管理者bootstrap用のRender Secret）
- `ADMIN_INITIAL_PASSWORD`（管理者bootstrap用のRender Secret）
- `STORY_MANGA_DATA_DIR`

SQLite、生成画像、Knowledge、Exportは`STORY_MANGA_DATA_DIR`配下へ保存するため、Renderでは永続ディスクを使用します。初回接続時のGitHub/Render連携、Blueprint作成、`OPENAI_API_KEY`のRender Secret登録は完了済みです。日常の更新は`main`へのpushとCI成功だけで進みます。Production QAではhealth、Story Analysis、Character Bible、Storyboard Job、Knowledge-aware QA、1枚の画像生成、Reload、Preview、PDF/ZIP Exportを確認済みです。

公開後は次の安全な軽量確認を実行できます。キーや本文は送信・表示しません。

```bash
.venv/bin/python scripts/production_smoke.py --url https://story-to-manga-b6bb.onrender.com
```

デプロイ失敗時はGitHub Actionsの失敗したcheckを修正して`main`へ再pushします。Render側のBuild/Runtimeログで起動・Health Checkだけを確認し、同じ設定のまま再デプロイします。Deploy HookはGit自動デプロイと二重化するため、通常は使用しません。デモ画像はPNGで保存され、PDFには生成済み画像とアプリ側のセリフを埋め込みます。
