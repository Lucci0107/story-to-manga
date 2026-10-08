# Story to Manga

物語を、読める漫画へ変換する制作ワークスペースです。

## 起動

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

ブラウザで `http://127.0.0.1:8000` を開き、「デモデータで試す」から主要フローを確認できます。共有Demoアカウントは制作内容と外部AIコストを共有するため、本番では既定で無効です。ローカル以外で有効化する場合だけ`ENABLE_DEMO_LOGIN=true`を明示してください。

## 環境変数

`.env.example` を参照してください。`OPENAI_API_KEY` が未設定の場合はデモAIとデモアートが動作します。実AIを利用する場合は、サーバー側の環境変数またはプロジェクト直下の`.env`だけにキーを設定し、`AI_PROVIDER=openai` と `IMAGE_PROVIDER=openai` を指定してください。キーはクライアントへ渡しません。

テキスト処理はOpenAI Responses APIのStructured Outputs（JSON Schema）を使い、設定画面で工程ごとのモデルを選択できます。自動・バランスは`gpt-6.1-sol`を中心に、確認工程は`gpt-6-luna`、最高品質は`gpt-6-astra`、コスト優先は`gpt-6-luna`を使います。推論強度は自動・low・medium・high・xhigh・maxに対応し、自動はモデルの標準値を使います。[OpenAIのモデル仕様](https://developers.openai.com/api/docs/guides/latest-model)を2026年10月4日に確認しました。

画像生成の既定値は`gpt-image-2.5-sunburst`で、高速制作向けの`gpt-image-2.5-flare`も選択できます。コマ比率に合う任意サイズと保存済みトリミングを引き継ぎ、生成品質は従来どおり`low`です。[画像APIの仕様](https://developers.openai.com/api/docs/guides/image-generation#customize-image-output)に合わせています。設定画面の「最新の標準設定を選択」から保存すると、最新の既定値へ戻せます。既存の個別モデル設定・プロジェクトの指定・生成済み画像は保持します。

設定画面を未導入の既存利用者やモデル指定のない直接呼び出しには、`OPENAI_TEXT_MODEL`（既定`gpt-6-luna`）と`OPENAI_IMAGE_MODEL`（既定`gpt-image-2.5-sunburst`）を使います。Astraが利用できない場合はGPT-6.1 Solへ、GPT-6.1 SolとGPT-6 Lunaが利用できない場合は同系列の5.6モデルへ一度だけ切り替え、実際のモデルと推論を履歴に記録します。通信障害や認証エラーではモデルを切り替えません。

通常のテキスト処理とは別に、Storyboardは`OPENAI_STORYBOARD_TIMEOUT_SECONDS`（既定240秒）と`OPENAI_STORYBOARD_MAX_RETRIES`（既定1回）で大きなStructured Outputへ対応します。すべての再試行回数と最大出力トークンには上限があります。

人物設定は、主要人物の提案 → ユーザーによる対象の選択・確定 → 選択した人物だけの詳細生成、の順で進みます。候補提案には原稿全体を使い、章／区間の出現率、名前の言及回数、AIによる重要度と理由を保存します。出現率は名前・別名・抽出根拠による推定で、代名詞だけの登場を完全には数えません。初期の推奨は重要度と頻度の順に最大12人とし、その他の候補もユーザーが追加できます。候補数と詳細設定の保存上限64人を区別します。

`POST /api/projects/{id}/character-proposal` は提案までで停止します。再読み込みでは保存済み提案を使い、同じ原稿・解析への再要求も再利用します。明示的な再提案だけ `refresh: true` とします。`PUT /api/projects/{id}/character-proposal/selection` は選択だけを保存します。`POST /api/projects/{id}/characters` には `proposal_id` と `selected_candidate_ids` が必要で、この要求が選択の確定です。原稿・解析の更新後や、未確認の要求では詳細生成を開始しません。ネーム生成から人物設定を自動生成する経路もありません。

確定後のネーム・AI品質確認には選択した人物設定だけを渡し、コマ生成にはそのコマの登場人物だけを渡します。選択から外した保存済み設定は削除せず、保管欄から確認・編集できます。既存のコマでその人物が使われている場合も引き続き設定を参照します。

人物処理は`OPENAI_CHARACTER_MAX_OUTPUT_TOKENS`（既定25000、最大32000）と`OPENAI_CHARACTER_TIMEOUT_SECONDS`（既定300秒）で推論と出力の余地を確保します。1回の詳細設定は4人までとし、出力上限またはtimeoutになった区間・人物設定のバッチだけを分割します。設定済みの人物と途中まで完了した設定を保存・再利用し、再試行では未完了の対象だけ生成します。既存の手動設定を上書きしません。引用は原文と照合して保持し、原稿・Knowledgeを命令として実行しません。

## 対応形式

- 直接入力
- `.txt`
- `.md`
- `.pdf`
- `.docx`

アップロード上限は5MBです。本文は50万文字以内で、抽出不能・空ファイルは拒否します。PDF/docxは圧縮後サイズだけでなく抽出後の本文量も制限し、過大な展開を拒否します。

## 主な画面

- `/login`: ログイン、新規登録（デモログインは開発環境のみ既定有効）
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

## AIによる漫画化設定の初期提案

Story Analysis完了後に漫画化設定を開くと、既存のAnalysisを主な入力として、目標ページ数、視覚スタイル、色、テンポ、セリフ量、想定読者、簡潔な理由、シーン別ページ配分をStructured Outputsで推奨します。シーン数、主要展開、葛藤、アクション、感情の間、場面転換、クライマックス、結末などを複合評価するため、固定40ページには依存しません。

推奨値はフォームへ初期表示されますが、保存前に編集できます。保存済みのユーザー設定は再表示時に上書きされません。「分析結果から再提案」はプレビューを表示し、「推奨値を適用」を押した場合だけ設定へ反映します。Analysis更新後は推奨をstaleとして表示し、既存設定を変更しません。AIが利用できない場合は、同じAnalysisから作る決定論的なフォールバックを表示します。

推奨生成は`settings_recommendation_model`でモデルを選択でき、既存のGlobal / Project AIモデル設定、Knowledgeの`adaptation` Scope、Processing Dialogを再利用します。言語と読み方向はProjectの固定ルールを優先し、推奨値から変更しません。

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
- 本番の共有Demoログインは既定で無効にし、認証済み画面とAPIはブラウザキャッシュへ保存させません。
- Project、生成画像、書き出しファイルは所有者確認後に返します。
- Project削除時は、そのProjectの生成画像とExportもStorageService経由で削除します。
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
- `OPENAI_STORYBOARD_TIMEOUT_SECONDS`
- `OPENAI_STORYBOARD_MAX_RETRIES`
- `OPENAI_MAX_OUTPUT_TOKENS`
- `OPENAI_CHARACTER_MAX_OUTPUT_TOKENS`
- `OPENAI_CHARACTER_TIMEOUT_SECONDS`
- `STORYBOARD_JOB_STALE_SECONDS`（既定900秒。中断Jobを再試行可能に戻す判定時間）
- `STORYBOARD_BATCH_PAGES`（既定8ページ。大きなネームのStructured Output分割単位）
- `MAX_UPLOAD_BYTES`
- `SESSION_DAYS`
- `ENABLE_DEMO_LOGIN`（本番では未設定のまま無効化を推奨）
- `ADMIN_EMAIL`（管理者bootstrap用のRender Secret）
- `ADMIN_INITIAL_PASSWORD`（管理者bootstrap用のRender Secret）
- `STORY_MANGA_DATA_DIR`

SQLite、生成画像、Knowledge、Exportは`STORY_MANGA_DATA_DIR`配下へ保存するため、Renderでは永続ディスクを使用します。初回接続時のGitHub/Render連携、Blueprint作成、`OPENAI_API_KEY`のRender Secret登録は完了済みです。日常の更新は`main`へのpushとCI成功だけで進みます。Production QAではhealth、Story Analysis、Character Bible、Storyboard Job、Knowledge-aware QA、1枚の画像生成、Reload、Preview、PDF/ZIP Exportを確認済みです。

公開後は次の安全な軽量確認を実行できます。キーや本文は送信・表示しません。

```bash
.venv/bin/python scripts/production_smoke.py --url https://story-to-manga-b6bb.onrender.com
```

デプロイ失敗時はGitHub Actionsの失敗したcheckを修正して`main`へ再pushします。Render側のBuild/Runtimeログで起動・Health Checkだけを確認し、同じ設定のまま再デプロイします。Deploy HookはGit自動デプロイと二重化するため、通常は使用しません。デモ画像はPNGで保存され、PDFには生成済み画像とアプリ側のセリフを埋め込みます。
