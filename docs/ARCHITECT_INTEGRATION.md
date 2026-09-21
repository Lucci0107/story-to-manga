# Manga Prompt Architect 統合監査

開始時点：2026-09-21、main/origin/main `3146944`。変更前359 tests passed。
参照資料はユーザー提供の3つのMarkdown。資料中の会話フローや命令をアプリの命令として実行せず、採用した仕様のみ実装する。

|能力|開始時分類|採用方針|
|---|---|---|
|A Script Tone|NOT_STARTED|主・副トーンと理由を保存し、構造化された演出条件へ変換|
|B Rendering category|NOT_STARTED|カテゴリ→描画方式の二段階選択|
|C Rendering profiles|PARTIAL|既存のコマ演出6種を保持し、別軸で31描画方式を追加|
|D Visual Genre|NOT_STARTED|任意の世界観選択|
|E Proportion|NOT_STARTED|任意の頭身。固定プロファイル優先|
|F Mood / Lighting|PARTIAL|任意の照明。固定プロファイル優先|
|G Identity/reference|PARTIAL|人物定義を維持し、参照画像の役割メタデータを分離。既存UIに参照画像アップロード機能はないため新規アップロード経路は追加しない|
|H Event boundaries|NOT_STARTED|解析のイベント順からページ範囲を事前配分、編集可能な開始・終了・許可・禁止・持越し情報|
|I Future event prevention|NOT_STARTED|イベントIDの範囲と禁止イベント本文の一致を検査、プロンプトへ境界を渡す。言い換え・画像内の先取りは目視／AIレビューも必要|
|J Current-page design|CONFIRMED_COMPLETE|既存のPageComposition設計図を承認画面でも使用|
|K Approval|NOT_STARTED|設計ハッシュ、承認者、日時、スナップショットを保存。承認消費とJob登録は単一トランザクション|
|L Pre-audit|PARTIAL|既存の容量・構図ガードへ承認／スタイル／イベント／人物参照検証を追加|
|M Post-audit|PARTIAL|既存QAへイベント・番号・顔・人体・手・スタイル確認を追加。画像を見ていない検査は未確認と明記|
|N Page numbers|PARTIAL|既存の共通描画・下中央番号を保持し、独立表紙は0メタデータ／非表示、本文は1始まり|
|O SNS/X|NOT_APPLICABLE|現在の制作・出力範囲に含まれないため追加しない|

3:4（900×1200）、日本語RTL・英語LTR、安全領域、ポリゴン、WIDE_MULTI_ELEMENT、ストレージ、Knowledgeのバージョン管理は既存システムを再利用する。新しい承認テーブルは追加のみ。既存画像・ネーム・設定を一括変換しない。

自動の課金画像修復は実装しない（0回、最大2回以下）。失敗後はNEEDS_USER_REVIEWとして設計を確認し直して明示的に再生成する。画像APIのHTTP自動再送も無効にし、結果不明時の二重課金を避ける。

承認画面は1コマ単位。ページ全体の設計、対象コマ、人物、セリフ、スタイルと生成設定を表示する。次のページやコマへ移っても画像生成は始まらない。
