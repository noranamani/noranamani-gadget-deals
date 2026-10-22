# 既存日曜06:40 JST note編集タスク用プロンプト（移行承認後）

AGENTS.mdを読み、最新mainの同じコミットからdata/products.json、data/history.json、data/editorial.json、data/collection-status.jsonを確認してください。

当日06:10以降の商品取得と06:30の編集が同じproductsSha256に対応し、全カテゴリが揃っている場合だけ、根拠データを使ってdata/note-weekly.jsonだけを更新します。不整合・未更新なら停止し報告してください。

generatedAtは実際の編集時刻（タイムゾーン付きISO 8601）で日次編集以降、sourceProductsSha256は同じコミットのcollection-status.jsonのproductsSha256を転記します。weekLabelは当週月曜〜当日日曜のYYYY/MM/DD〜YYYY/MM/DD。featuredは現在の掲載商品のコードから最大5件とし、仕様や数値を推測しないでください。AGENTS.mdの週次編集ルールに従います。

JSONと差分を確認し、変更はdata/note-weekly.jsonだけを単一コミットでmainへ通常pushします。mainが元コミットから進んでいれば停止し、force pushしません。API取得、記事・画像生成、note投稿、サイト公開、タスク追加・変更はしません。秘密値を出力しません。記事・画像・ZIP・manifest生成と保存は06:50 Actionsが担当します。

参照コミット・商品更新時刻・編集の成否を報告してください。
