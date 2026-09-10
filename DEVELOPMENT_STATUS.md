# Story to Manga 開発状況

更新日: 2026-09-10

## 生成前のページ設計・意味別演出（2026-09-10、実作品検証待ち・未リリース）

- ブランチは `codex/composition-readability-fix`。`6953738` の安全修正を維持。GitHub mainは読み取り確認で `06c478b6873c3a0ab4e49560b833572eb59e66ce`。本変更のmerge/push/CI/Render反映は未実施。
- 新規の未生成v3コマに `PanelDirection` を保存。人物・顔・重要な手/小物、実測したセリフ・地の文・SFX予約領域、crop anchor、スタイルを画像生成前に計画。入力署名の不一致・文字不足はJob作成前とWorkerの生成前に拒否。既存画像へこの計画を自動適用しません。
- Storyboardの構造化出力に発話種別・SFX種別・人物位置を追加。意味分類と保存済みVisual Styleから共通トークンを解決。通常発話・思考・叫び・小声等、足音・衝撃・環境音等を分け、固定オレンジを排除。生成済み画像の構図不整合は未解決として扱います。
- 設計済みページはPreview/PDF/ZIPで同じ画像描画を使用。生成画面に全ページの設計図と確定状態・スタイル表示を追加。PDFへの個別の見栄え補正ではありません。
- 最初の実画像QAは承認上限の2枚を実施。隔離ローカルの架空旅行者、新規QA作品、各1リクエスト・自動Retryなし。左右の人物/文字空間分離は確認できた一方、最終コマで頭頂の切れを確認し、この時点の視覚ゲートは不合格。元の本番Artworkは未送信・未変更。
- 原因の一つとして、任意サイズ対応のgpt-image-2を旧モデル用3サイズへ制限していた点を特定。公式SDK仕様を確認し、約1MP・16px刻み・最大3:1でPanel比率へ近似する要求へ修正。旧モデルは従来の対応サイズを維持。
- 追加1コマの確認を質問し、ユーザーの「続けて」を受けて3枚目だけ実施。約2.0813:1のコマに対し1568×752を要求し、実出力も1568×752。頭頂・挙げた手と左側セリフの空間分離を目視確認。合計3枚、各1リクエスト、自動Retryなし。画像データの加工による修正ではなく、生成キャンバスの比率指定による改善です。これは1コマの検証であり、実4ページ作品の合格ではありません。
- 3枚目を含む設計ページのPreview PNG・PDF埋込画像・ZIP PNGはピクセル一致を確認。PDFをPopplerでレンダリングして頭・手・文字の配置を目視。周囲の4コマは設計図で、完成漫画として提供していません。
- 公式根拠: https://github.com/openai/openai-python/blob/main/src/openai/types/image_generate_params.py （2026-09-10確認）。新モデル・新providerの追加はありません。
- ローカルBrowser: 生成前の4ページ設計、保存後reload、Desktop/Dark、390px/Dark・LightのPreviewを確認。幅390に対しdocument幅375、console error/warning 0。これは架空QAデータであり、実4ページ作品の合格証拠ではありません。
- 本番の利用可能なin-appセッションはdemoユーザー。対象の実4ページProjectは一覧に存在せず、保存Visual Style/元コマ画像を未取得。Downloadsには対象PDFのみ確認。実作品ZIPまたは対象Projectへアクセス可能なセッションが必要です。デモProjectの設定を実作品の設定と混同しません。
- 最終回帰 **224 passed**。画像API境界・スタイルgutter・Storyboard schemaのテストを含みます。JS構文・diff check成功。compileallはOSキャッシュ書込制限を避け、QA用のPYTHONPYCACHEPREFIXで成功。pip check依存関係エラーなし。未完了: 実作品の生成前再設計、実4ページの視覚QA、全工程Browser QA、本番リリース。
- `MANGA_KNOWLEDGE_CATEGORIZED_PACK/` は未変更・未追跡のまま。Knowledge参考文書は `docs/knowledge/semantic-manga-composition.md` のみ追加し、Libraryへ自動登録していません。

## コマ割りと画像生成の整合修正（2026-09-10、継続中）

- 既存APIキーの再利用をユーザーが承認。キー値の表示・新規保存・外部API呼出しは行っていません。
- ページ相対座標をそのまま画像比率にしていた不一致を修正。900×1200の実寸とArtwork viewportを含む共通比率を、画像プロンプトと対応生成サイズ選択に使用します。
- 意味上の主役コマを段構成にも渡し、重要度同点時に別のコマを独立大コマにする不一致を修正。既存保存済みページの自動再配置はしません。
- `docs/knowledge/semantic-manga-composition.md` に面積差、生成前の文字予約、選択的越境、実画像検証の参考規則を追加。Libraryや既存Projectには未適用。
- 検証: pytest **186 passed**、JavaScript構文、diff check成功。今回の差分のBrowser QA・CI・本番デプロイ・実画像生成は未実施。
- 未完了: 生成前空間設計の全工程検証、吹き出し・擬音表現の改善、実作品によるPreview/PDF/ZIPの視覚検証。既存の保守的文字帯fallbackだけでは漫画品質の完成とは扱いません。

## 出力PDFの可読性修正（2026-09-10、実作品の再検証待ち）

- ユーザー提供PDF全4ページで、元絵の楕円複製、人物と文字の重なり、小コマの文字不足、不自然な枠を確認。下記9月9日の「COMPLETE」「保証」は実作品の漫画品質を立証するものではなく、今回の目的は未完了です。
- ローカル修正: v3再配置で偽の人物Breakoutを作らない。矩形fallback時に頂点も修正。文字入りの段に最低高さを配分。初ページタイトルの領域を確保。保護領域との衝突を配置失敗として扱う。
- 既存Artworkの人物位置が未確認の場合、推測で文字を重ねず文字専用帯とArtwork viewportを分離する保守的fallbackを追加。実測フォント幅・日本語禁則による折返し、本文17px（900px幅基準）、丸いセリフ枠と墨色の文字を利用。これは完成漫画の演出品質を保証するものではなく、実作品で過大な文字帯・cropを再評価する必要があります。
- 実測文字配置のPreviewは所有権確認付きのPNGエンドポイントを使用。PDFは同じ900×1200画像・3:4ページ比率を利用し、ZIPのPNGとピクセル一致をテスト。旧形式のページ比率は維持。文字領域が不足するv3の書き出しは422として修正を案内し、無理な縮小で成功扱いしない。
- 「配置を再計算」は変更内容を確認してから指定ページだけをv3で再計算。元画像・セリフ・他ページは維持し、課金生成なし。本番既存作品には未適用。
- 検証: pytest **176 passed**、JavaScript構文、compileall、pip check、diff check成功。ローカルのデモArtwork4ページ（会話・コメディ・アクション・心理）でPreview/PDF/ZIP確認。Desktop、390px、Light/Darkの表示とconsole error 0件を確認。実作品ではなく合成検証用データでの結果です。
- 未完了: 提供PDFと同一作品の元Artwork/編集用JSONを含むZIPでの修正前後比較、実人物の顔・頭のcrop検証、吹き出しと地の文の表現調整、新規Artwork生成前の空間計画との整合、本番QA。現在の変更のCI・Renderデプロイは未実施。元画像なしのPDFから人物・背景を復元したり、無断再生成したりしません。
- `MANGA_KNOWLEDGE_CATEGORIZED_PACK/`は変更・削除・commit対象外。

## 現在の状態

ローカルで主要なStory to Manga制作フロー、Project横断Knowledge Library、OpenAI実AI接続とモデル選択・フォールバックを確認できるMVPです。GitHubの`main`へ接続・pushし、GitHub Actions CI成功後にRenderへ自動デプロイできる状態を確認済みです。永続化層はSQLite／ローカルStorageを維持したまま、将来のPostgreSQL／S3互換Storageへ移行できる境界を追加済みです。本番URLでSmoke Check、低負荷Production QA、管理者認証確認まで完了しています。言語・読順ロック機能と、Story Analysis由来のAI漫画化設定推奨も実装・ローカル・本番検証済みです。新規Projectではactive/readyな推奨Knowledgeをカテゴリ別Scope付きで自動選択し、既存Projectはユーザーが明示適用するまで変更しません。QAではKnowledge未選択、Scope不一致、処理中、無効、参照成功を区別し、利用Document/Versionを確認できます。

## Semantic Manga Composition v3 最終検証（2026-09-09）

- 既存PageComposition v2を保持したまま、新規Projectの既定をv3へ分離しました。矩形を標準とし、面積差・主役コマ・ページの意味（dialogue / action / comedy / psychological / establishing / climax）から構成を決定します。意味的理由のない台形・斜め境界・large bleedは矩形へ戻し、静かな会話／心理／導入ページの装飾を抑制します。
- v3 Compositionへ、effect budget、dominant panel、shape reason、text safe zone、protected face zone、crop anchor、breakout reason／max extension、ページOverlay理由を保存しました。Breakoutは既定OFF、通常0〜1件、特殊ページでも最大2件に制限し、人物は隣接コマの保護顔領域・文字・セーフマージンと衝突しないよう検査します。吹き出しは自コマ内を優先し、明示された演出時だけgutterへ移します。
- 画像生成Promptへsemantic family、Panel shape／理由、target aspect ratio、crop anchor、文字予約領域、顔を置かない領域を注入しました。Rendererはcover cropでPanelを満たし、ページ背景→Artwork→border→Breakout→文字→ページOverlayの共有レイヤー順で描画します。Preview／PNG／PDF／ZIPは同じCompositionを利用し、Breakoutがセリフを覆わないことを保証します。
- Previewのv2/v3ではPanel相対の吹き出し・ナレーション・SFXをページ共通の文字レイヤーへ変換し、Breakoutの後・文字の前面へ描画するDOM順を固定しました。`moved_text_items`は重複描画せず、サーバー側PNG／PDF／ZIPとのレイヤー順をそろえています。静かなページのeffect budgetは指定値で過剰拡張できず、特別演出を明示したAction／Comedy／Climaxのみcharacter Breakoutを最大2件まで許可します。
- QAへ読みやすさ・視覚階層・顔可視性・文字衝突・面積差・装飾密度の決定的スコアと、過剰変形／過剰越境／顔・文字衝突の検出・保守的簡略化を追加しました。既存v2は暗黙にv3へ更新せず、Artwork再生成も行いません。
- v3回帰テストを追加し、既存を含む`pytest`: **163 passed**、`node --check static/js/app.js`、`python -m compileall -q app`、`git diff --check`が成功しました。ローカル新規ProjectのPreviewでComposition v3、心理ページの矩形主体・不均等面積、アクションページの意味付けされた斜め要素、RTL/LTR、Preview/PDF/ZIP同一PNGを確認しました。画像生成APIは呼び出していません。
- 本番は既存Project「ある外科医の思考 v2」を読み取り専用で再読み込みし、4ページ／19コマ／19枚生成済みを維持したままPreview／QA／Exportを確認しました。既存v2のpolygon／Breakoutは保持し、新v3への自動再計算は行っていません。iPhone SE（375×667）相当、Desktop、Light／Darkを確認し、DevTools Consoleをクリア後に再読み込みして0 messagesでした。
- 初回本番確認で検出した処理ダイアログ終了時の`aria-hidden`フォーカス警告は、復帰フォーカスを先に移す局所修正（`4084927`）で解消しました。最終CI `34359422531`とRender deployment `dep-dagm7tijnfac73fbk1o0`はsuccess、`/api/health`とProduction smoke（health／login／CSS／JavaScript）は全項目HTTP 200です。
- 文字レイヤー修正コミット`ee6ac39`のGitHub Actions run `34360955686`はsuccess。CI後のRender切替中に一時502を観測しましたが、復旧後の`/api/health`はHTTP 200、配信`app.js`へ`compositionPanelTextMarkup`が反映されています。本番既存Projectは読み取り専用で再確認し、Previewはページ共通文字層、PDF／ZIPは処理完了、推奨Knowledge付き新規Project画面、iPhone SE相当／Desktop、Light／Dark、Console 0 messagesを確認しました。
- 実装コミットは`c39e5c1`、フォーカス修正コミットは`4084927`、文字レイヤー／保守的budget修正は`ee6ac39`です。`MANGA_KNOWLEDGE_CATEGORIZED_PACK/`はユーザー所有の未追跡データとして今回も変更・削除・commitしていません。

**9月9日時点の機能検証記録: 実作品の品質保証としてのCOMPLETE判定は撤回。上記の出力PDF再検証を継続。**

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
- 新規Projectの推奨Knowledge自動選択、カテゴリ別default Scope、既存Project向けの非破壊な推奨・明示適用
- QAのKnowledge解決5状態、実参照Document/Version表示、Document/Version/Chunk/Scope traceability
- PageComposition v2を追加：ページ相対polygon／台形／斜めgutter、不均等面積、重要コマのvisual hierarchy、cover cropと焦点anchor、ページ単位のbreakout／bubble／SFX／title overlayを一つのJSONとして保存します。既存Composition v1は自動変更せず、明示的なlayout repairだけで新版へ再計算します。
- Preview、PNG、PDF、ZIPは同じPageCompositionをsource of truthとして描画し、通常コマのletterboxを作らず、PDFは合成画像と検索可能な日本語テキストを併記します。画像再生成や外部画像サービス追加は行いません。
- 画像生成前に確定したPanel shape、target aspect ratio、crop anchor、safe zone、breakout intentをPromptへ渡し、OpenAI Images利用時はwide／portrait／squareの近い生成サイズを選択します。
- PageComposition v2の回帰テスト（polygon、cover crop、breakout、page overlay、RTL/LTR、legacy互換、出力同一性、coverage／uniform QA）を追加しました。
- Character Bible生成Jobを永続化し、外部AI経路をqueued→processing→completed/failedへ収束させました。Project保存とCharacter Job完了／失敗を同一transactionで確定し、入力検証・安全なエラー分類・stale／中断復旧・同一Projectの重複Job抑止を実装しています。
- Character画面は202応答のJob IDを受け取り、単一poller、再読み込み後のserver state復元、通信状態不明時の非破壊導線、失敗時Retry、完了／失敗後のボタン・次工程同期を行います。Demo providerの既存同期互換とFollow-upのProject状態は維持しています。
- 物語解析、漫画化設定、キャラクター、ネームの編集と永続化
- ページ・コマの追加、削除、並び替え、レイアウト変更
- コマ単位の生成Job、状態表示、再試行、重複リクエスト抑止
- 外部AIの長時間Storyboardを永続Jobへ登録し、HTTPタイムアウトを避けるバックグラウンド処理とUIポーリング
- Storyboard Jobのheartbeat／stale recovery、保存と完了状態のatomic更新、reload時のserver state復元、失敗時Retry復帰
- 9ページ以上のStoryboardを既定8ページ単位のStructured Outputへ分割し、最大120ページまでの応答切断リスクを抑制
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
- Knowledgeのレイアウト原則を反映した決定的なPage layout engine（標準ドラマ／会話／アクション／心理／4コマ）
- Panelの役割・scene type・重要度に基づく不均等geometryと、重要コマの大面積化、日本語RTL／English LTRの物理配置
- 吹き出し・ナレーション・SFXの共通collision pass、6%セーフマージン、人物保護zone評価、長文折り返し／overflow検出
- 保存済みgeometryをPreview・PDF・ZIPページ画像で共用し、Artworkを再生成しないPage単位の配置再計算を追加
- PDF／ページ画像へOFLのM PLUS 1pを埋め込み、閲覧環境の外部CMapへ依存せず日本語を表示

## 検証済み

- Knowledge自動選択修正の全回帰: `pytest` 119 passed、`compileall`、JavaScript構文、`pip check`、`git diff --check`が成功
- Knowledge自動選択のローカルBrowser QA: 新規Projectの推奨Layout初期ONとQA実参照、既存未設定Projectの3択warningと明示適用、Scope不一致表示、OFF設定のreload保持を確認
- QA画面を390px／1440px、Light／Darkで確認し、横overflowなし、error overlayなし、console error 0件。画像生成は実行していません
- Knowledge自動選択コミット`f478ee3`のGitHub Actions CI run `34310984043`はsuccess、Render deployment `dep-dage0867bikc7393nv2g`もsuccess
- Knowledge自動選択のProduction QA: QA専用Layout Knowledge v1と最小合成Projectを新規作成し、推奨設定の初期ON、優先度70、follow-latest、storyboard/page_layout/panel_prompt/quality_check/export Scopeを確認。Knowledge-aware QAで1 Version / 1 Chunkを実参照し、Document/Version/Chunk/quality_check Scope traceabilityの保存とreload後の保持を確認
- Productionの既存2 Projectは検証前後で8ページ／31コマ／2生成済み、および8ページ／34コマ／0生成済みを保持。Desktop 1280pxとMobile 390px、Light/Darkで横overflow・error overlayなし、console error 0件。画像生成は実行していません

- `pytest`: 86 passed（Storyboard Job成功・失敗・validation例外・重複防止・stale recovery・heartbeat・48ページ分割・RTL/LTR・frontend復元を含む）
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
- 全体最適化レビューのProduction QA: health/login/CSS/JavaScript 200、共有`/demo` 404、未認証admin API 401、HSTS/no-store、新CSS配信を確認。実ブラウザで390px/1440px、Light/Dark、Dashboard、新規Project、Project工程、AI推奨設定、日本語RTL Preview、PDF/ZIP導線、Knowledge、AIモデル設定を確認し、console error/warningは0件。既存productionデータの生成・削除・再保存は行っていません。
- Storyboard Job同期のローカルBrowser QA: 成功、強制失敗、processing中reload、完了後reload、Retry復帰、390px、Light/Darkを確認。完了後はネーム表示とボタン復帰、失敗後はエラーとRetryを表示し、console errorは0件
- Storyboard Job同期のProduction QA: 短い合成Projectを実AIで1回だけ再生成し、processingからcompletedへの収束、8ページ保存・表示、`gpt-5.6-sol`実行表示、ボタン復帰、reload保持、コマ生成画面への次工程遷移、console error 0件を確認。画像生成は実行していません
- 最新修正コミット`b889fe6`のGitHub Actions CI（run `34179819425`）がsuccess。CI通過後のRender自動デプロイを本番ページの修正反映（再提案ボタン有効化）で確認し、`/api/health`はHTTP 200、OpenAI provider表示も確認
- 最終管理者確認コミット`43aa035`のGitHub Actions CI（run `34121680039`）とRender自動deploy（`dep-dafaq6eq1p3s73dofjj0`）がsuccess。直後の一時502回復後、最終Production smokeのhealth/login/CSS/JavaScriptが全項目HTTP 200
- 本番検証用Projectは合成データのため削除せず保持しています。ユーザー操作なしの本番データ削除は行っていません。
- Storyboard OpenAI timeout/resilience修正（`f4f216f`）: 既存のJob lifecycle・8ページbatch・重複Job防止は維持し、Storyboard専用の`240秒 / 1回` timeout・bounded retryを追加しました。認証、権限、model access、quota、rate limit、timeout、server、response validationをサーバー側で分類し、quota／認証等は無駄に再試行しません。
- Storyboardのリクエストには同一試行系列を追跡できる`X-Client-Request-Id`を付与し、モデル・timeout・試行回数・入力文字数・所要時間などの秘密を含まない診断メタデータだけをログへ記録します。Story全文・Knowledge全文・API keyはログへ出力しません。
- Storyboard batchの文脈を見直し、長文・複数batchでは原文全文を繰り返し送らず、階層化アウトライン、分析結果、必要な設定・キャラクター情報、言語／読順、限定Knowledgeを利用する構成にしました。batchサイズは実測で8ページを維持し、無条件の細分化は行っていません。
- Storyboard timeout／quota分類、同一request id、task-specific timeout、長文batch context縮小の回帰テストを追加し、全体pytestは`90 passed`です。compileall、JavaScript syntax、pip check、diff check、secret scanも成功しました。
- ローカル実AI検証: 短編Storyboard（3ページ）と9ページStoryboard（8+1ページbatch）を成功確認しました。画像生成は追加実行していません。
- 最新Production QA: `https://story-to-manga-b6bb.onrender.com`で合成ProjectのStoryboardを実AIで1回だけ生成し、`processing → completed`、8ページ・34コマ表示、実行モデル`gpt-5.6-sol`、Processing Dialog終了、ボタン復帰、reload後のネーム保持、`コマ生成へ`の次工程遷移を確認しました。画像生成は実行していません。
- Panel生成の全体進捗UXを追加：既存Processing Dialogへ、同一生成リクエスト単位の`batch_id`に基づく`completed / generating / waiting / failed`件数、現在のページ・コマ、heartbeat時刻を接続しました。active JobがwaitingだけでもDialogを維持し、個別再生成では対象コマだけを集計します。
- Panel生成のreload・画面遷移復元と通信耐性を追加：server-sideのactive Jobを再取得してDialogとpollingを復元し、一時的なpolling失敗ではJobを停止せず、連続失敗・長時間未収束時は「状態不明」と再読み込み操作を表示します。既存のstale recovery、duplicate防止、個別Retryを維持しています。
- Panel Jobの状態不明処理を強化：連続したtimeout／network／429／5xxではbounded reconnect後にblocking Dialogを閉じ、画面内の非ブロッキング警告から「状態を再確認」または「再読み込み」へ誘導します。401/403は再ログイン導線、404はProject本体の一度だけの再取得へ分岐し、AbortController・run token・単一pollerで古い応答の上書きと永久pollingを防止します。
- 対応するactive Jobを失ったqueued/processing Panelをserver-sideでfailed／Retry可能へ収束させる孤児状態復旧を追加しました。既存のstale recovery、atomic保存、重複生成防止、個別再生成は変更していません。
- Panel Jobの完了・失敗をProject保存と同一transactionで確定し、stale失敗Jobを遅いBackgroundTaskが再開・completedへ戻さないRepository境界を追加しました。ローカルdemoの一括生成、waiting/generating表示、reload復元、完了後のDialog終了と操作復帰を実ブラウザで確認しました。
- `f4f216f`のGitHub Actions CI（run `34226922292`）はsuccess、Render自動deploy後の`/api/health`はHTTP 200、Production smokeのhealth/login/CSS/JavaScriptも全項目成功しました。
- Panel生成のProduction QA: 本番の新規合成ProjectでStory Analysis、AI漫画化設定、Character Bible、8ページStoryboardを実AIで生成し、画像生成は個別コマを2枚だけ実行しました。1枚目の`completed / gpt-image-2 / revision 1`保存、2枚目の生成中reload後の中央Processing Dialog復元（`0 / 1 完了・生成中 1`、ページ・コマ表示）、完了後のDialog終了、`2 / 31`保存済み表示、reload後の画像保持を確認しました。
- Panel生成のProduction UX: 390px viewportでactive JobのDialog復元とコマ進捗表示を確認し、横overflowなし（body scroll width 375px / viewport client width 375px）、Production console error 0件を確認しました。`/api/health`はHTTP 200、OpenAI/image provider表示、最新`app.css?v=8`／`app.js?v=9`配信も確認済みです。
- Panel生成の最新コミット`daeefec`（GitHub Actions run `34239836516`）はsuccess、CI通過後のRender自動deployで変更済みassetが本番へ反映されました。
- Panel Job状態不明修正のローカル回帰：`pytest` 96 passed、`compileall`、JavaScript構文、`pip check`、`git diff --check`が成功。隔離Demo環境で16コマの一括生成について、中央Dialog、実際のgenerating/waiting集計、terminal cleanup、reload後の生成済み保持、Dark theme、孤児Panelのserver-side Retry可能化を確認しました。実AI／追加画像生成は行っていません。
- Panel Job状態不明修正の初回本番検証：GitHub Actions run `34266695670`、Render deployment `dep-dag5o6gae00c738g456g`がsuccess。Production smokeのhealth/login/CSS/JavaScriptは全項目HTTP 200で、更新済み`app.js`の状態不明・再確認導線が本番配信されていることを確認しました。
- Panel Job状態不明修正のProduction Browser QA：既存の本番Project（31コマ、生成済み2枚）を変更・追加生成せずに、生成画面の表示、Dark theme、reload後の31コマ／2枚保持、Dialog非表示、画像revision保持を確認しました。追加の画像生成は行っていません。
- Manga layout engineのローカル回帰：`pytest` 112 passed、`compileall`、JavaScript構文、`pip check`、`git diff --check`が成功。標準ドラマ／会話／アクション／心理／4コマのgeometry、重要コマ面積、RTL/LTR、保存再読み、Page単位再配置、QA検出、PDF日本語フォント埋め込み、ZIP合成ページを検証。
- Manga layout engineのローカルBrowser QA：6コマの不均等配置、重要コマ32.3%、日本語RTL／English LTR、文字要素collision 0、長文overflow 0、Light/Dark、狭幅Previewの1列化、console error 0を確認。PDFと900×1,200pxのZIPページ画像を実際に書き出し、同一geometryと読みやすい文字配置を確認。PDFはPopplerによる画像化と日本語テキスト抽出にも成功。画像AI生成は行っていません。
- Production QAで旧均等2列グリッド基準の読順検査が新しい不均等geometryを誤検知することを確認し、layout version 2では保存済み行構成と行内RTL/LTR列順を検証する方式へ局所修正。壊れた列順は引き続き検出する回帰テストを追加しました。
- Manga layout engineの初回コミット`9c68c2c`はGitHub Actions run `34294142746`、読順QA整合修正`fe214e5`はrun `34294923564`でsuccess。両方ともCI通過後にRenderへ自動反映され、最終`/api/health`はHTTP 200です。
- Manga layout engineのProduction Browser QA：既存の8ページ／31コマ／生成済み画像2枚を保持したまま、layout version 2、不均等面積、日本語RTL、文字要素collision 0、overflow 0、PDF／ZIP生成、Processing Dialogの開始・終了を確認。再実行したKnowledge-aware QAでは、コマ読順、視覚的階層、吹き出し・ナレーション衝突がすべてOKとなりました。390pxでは横overflowなし、console error/warning 0件です。画像生成は追加実行していません。

## Character Bible Job 最終検証

- `pytest`: 143 passed。Character Jobの成功／失敗／構造化出力検証、例外分類、重複防止、stale／中断復旧、Retry、HTTP 202、task-specific timeout、既存Project状態保持を含みます。`node --check static/js/app.js`、`python -m compileall app`、`pip check`、`git diff --check`も成功しました。
- ローカル隔離Demoで、Character画面の生成開始（Processing Dialog）、queued／processing表示、再読み込み後のJob復元、stale／中断時のRetry表示、再試行成功後のCharacterカード・次工程復帰を確認しました。既存Characterは保持され、画像生成・外部有料操作は行っていません。consoleログは空でした。
- 本番既存Project「ある外科医の思考 v2」を認証済みセッションで読み取り専用確認し、Character画面の再読み込み後に旧「人物設定を作成中…」が残らず、再実行可能な「生成する」へ収束することを確認しました。Projectの生成・削除・上書き、Character再生成は行っていません。既存のDesktop／390px・Light／Dark・console 0回帰確認は維持しています。
- コミット `6a3bcdd` を `main` へpushし、GitHub Actions run `34327735049`（pytest／compile／JavaScript syntax／pip check）がsuccess。Render自動deploy `dep-dagh9iss728c73d5tebg`もsuccess、Production `/api/health`はHTTP 200、`scripts/production_smoke.py`（health／login／CSS／JS）は全項目OKです。

## In Progress

- なし。PageComposition v2およびCharacter Bible Job lifecycleの実装、ローカル回帰、CI、Render自動deploy、Production Browser QAを完了しました。既存Projectと`MANGA_KNOWLEDGE_CATEGORIZED_PACK/`は変更していません。

## PageComposition v2 最終検証

- コミット`ca09c36`を`main`へpushし、GitHub Actions CI run `34321761018`（pytest 134 passed、Python compile、JavaScript構文）がsuccess。
- Render自動deploy `dep-dagg8r0ae00c73bqlf3g`（GitHub deployment `6344377609`）がsuccess。切替直後の一時502回復後、`/api/health`はHTTP 200（`status: ok`）。
- ローカルBrowser QAで、dynamic 7-panelの大コマ／不均等面積／台形・斜め境界、cover crop、breakout foreground、ページ単位bubble／SFX／title overlayを確認。Preview・PDF・ZIPは同一Composition Modelを使い、画像再生成なし。
- 本番の既存Project「ある外科医の思考」は認証済みセッションで読み取り専用確認。QAのKnowledge使用中1件（漫画スタイル・画風設計 Version 1 / 4 Chunk）、日本語right-to-left、4ページ／19コマ／19生成済みを確認し、Preview・Export導線を表示しました。既存データの生成・削除・上書きは行っていません。
- 本番の新規Project画面で推奨Knowledgeが初期ON（Dialogue／Genre／Character／Layout／Style）で表示され、作成前に変更可能なことを確認。Project作成や画像生成は行っていません。
- 本番ChromeのDesktop表示とiPhone SE（375×667）相当表示を確認。再読み込み後のDevTools Consoleは`0 messages in console`で、UIの横overflow／error overlayは確認されませんでした。
- 本番へ配信された`app.css?v=12`／`app.js?v=13`はHTTP 200で、polygon／cover／breakout／composition実装を含むことを確認しました。既存Projectは互換性のためlegacy Composition v1のまま保持され、新版への再計算は明示操作時のみです。

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
- 全体最適化レビューcommit: `e57594a`、GitHub Actions `34187009638`（success）、Render `dep-dafot767bikc73eh73m0`（success）
- モバイルsidebar修正commit: `edb4db8`、GitHub Actions `34187418823`（success）、Render `dep-dafp0ebm8hqs73e86cb0`（success）
- Storyboard Job lifecycle修正commit: `5b695c3`、GitHub Actions `34222760101`（success）、Render `dep-dafvd1h42hec73dnbgeg`（success）
- Storyboard次工程遷移修正commit: `572c5a3`、GitHub Actions `34223384326`（success）、Render `dep-dafvgdss728c739cbc20`（success）

- Panel Job状態不明修正コミット: `b013bde`、GitHub Actions `34266695670`（success）、Render `dep-dag5o6gae00c738g456g`（success）
- Panel Job状態不明修正後のProduction smokeはhealth/login/CSS/JavaScript全項目HTTP 200。既存本番Projectの31コマ／生成済み2枚はreload後も保持され、追加の画像生成は行っていません。
- Character Bible Job lifecycle修正コミット: `6a3bcdd`、GitHub Actions run `34327735049`（success）、Render deployment `dep-dagh9iss728c73d5tebg`（success）。本番health 200、Production smoke（health／login／CSS／JavaScript）全項目成功。認証済み既存Projectの再読み込みでCharacter画面がstuck表示から再実行可能状態へ復帰することを読み取り専用で確認しました。

## 次回セッションの確認

1. `git status --short --branch`で作業ツリーと`origin/main`を確認する
2. 通常のデプロイは`main`へpushし、GitHub Actions成功後のRender自動デプロイを確認する
3. 本番変更がある場合だけ、影響範囲のpytestと`.venv/bin/python scripts/production_smoke.py --url https://story-to-manga-b6bb.onrender.com`を実行する
4. 外部PostgreSQL／S3互換Storageへの移行は、別タスクとしてデータバックフィル計画を作成してから行う
