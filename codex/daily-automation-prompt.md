# 既存06:30 JST編集タスク用プロンプト（移行承認後）

最初にconfig/automation-owner.jsonを確認してください。editorialOwner=windowsの場合はWindowsが編集担当です。取得・編集・公開の状況確認と報告だけを行い、ファイル変更・commit・push・Actions再実行・新しいタスク作成はしないでください。以下の編集手順は実行せず終了してください。

AGENTS.mdを読み、最新mainの同じコミットからdata/products.json、data/history.json、data/collection-status.json、config/queries.jsonを確認してください。

商品が当日06:10 JST以降に取得され、全カテゴリが揃い、collection-status.jsonのproductsSha256が当該productsのSHA-256と一致する場合だけ、data/editorial.jsonを更新してください。取得失敗・未更新・整合不明なら停止して報告してください。

generatedAtは実際の編集時刻（タイムゾーン付きISO 8601）とし、sourceProductsSha256には同じコミットのcollection-status.jsonのproductsSha256を転記します。カテゴリIDを固定せず全カテゴリのcategoryNotesを作り、topPickCodeは該当カテゴリ内の商品か空文字にしてください。AGENTS.mdの事実・表現ルールを守り、数値・時刻・ハッシュ・商品仕様を捏造しないでください。

変更はdata/editorial.jsonだけ。差分とJSONを確認して単一コミットでmainへ通常pushします。直前に元コミットからmainが進んでいれば競合として停止し、force pushや自動再編集はしません。API取得、HTML/note生成、publish.sh、サイト公開は実行しません。秘密情報を表示・保存しません。新しい定期タスクを作りません。

最後に、参照コミット、商品更新時刻、編集の成否を報告してください。
