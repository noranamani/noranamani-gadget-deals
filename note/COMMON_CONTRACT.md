# 3サイト共通 note 記事受け渡し仕様 v1

対象: ガジェット / はりまよりみち / 播磨ガチャ入荷ナビ。

## 位置
各リポジトリの `note/latest/` を投稿バッチへ渡す。既存ガジェットの `article.md`、`article-cover.png`、`note-post.zip` は変更しない。

## 追加する manifest.json
```json
{
  "schema_version": 1,
  "site_id": "gadget",
  "issue_key": "2026-W42",
  "status": "ready",
  "title": "記事タイトル",
  "article_path": "article.md",
  "cover_path": "article-cover.png",
  "source_url": "https://example.com/",
  "generated_at": "2026-10-15T00:00:00+09:00"
}
```
- `site_id`: `gadget`, `local`, `gacha` のいずれか。
- `issue_key`: 同一媒体・同一週を識別するキー（例: `2026-W42`）。再実行しても変えない。
- `status`: `ready` または `skip`。新着0件、根拠未確認なら `skip` とする。
- `source_url`: 投稿先への誘導元となる各サイトのURL。未公開の地域サイトは公開前に `ready` にしない。
- `cover_path`: 画像が存在しないときは `null`。投稿バッチは画像なしでも動作可能にする。
- `generated_at`: タイムゾーン付きISO日時。

## 投稿バッチの処理
1. `manifest.json` を検証してから `article.md` を読む。旧ガジェット互換モードでは manifest なしでも読み込みを許容する。
2. `status=skip` なら下書き作成も公開もしない。
3. `site_id + issue_key + SHA256(article.md)` を記録し、同一内容の再投稿を防ぐ。既に公開済みの同じ `site_id + issue_key` を更新する場合は、勝手に新規投稿しない。
4. noteログインは初回だけ人がブラウザで認証する。Cookie・認証トークン・ユーザーデータは GitHub に保存しない。
5. 既定は下書き作成。公開自動化はアカウント権限・動作を確認し、個別に有効化する。
6. noteログイン失効、CAPTCHA、ブラウザ変更、投稿失敗は停止して通知。認証回避や連続再試行をしない。
7. 投稿成功時だけ投稿ID、URL、時刻、記事ハッシュを運用環境の状態ファイルへ保存し、失敗時は保存しない。実際のnote投稿結果を得ずに成功扱いしない。

## 媒体固有の注意
- gadget: 価格取得時刻、アフィリエイト注記、価格履歴に基づく判定を維持。
- local: イベントの開催日時・中止・料金を推測しない。公式根拠、掲載基準と掲載許諾に従う。公開前は `skip`。
- gacha: 店舗新設/変更を「入荷」と呼ばない。公式ページへのリンクだけで在庫確認済みとしない。

## 次段階
地域とガチャで正規データの前回スナップショット差分から週次記事を生成し、このmanifestを出力する。その後note投稿バッチv0.3を複数リポジトリ入力へ対応させ、dry-run→下書き→確認付き公開の順に試験する。
