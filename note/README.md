# note weekly bundle

毎週の価格データから作る note 用記事セットです。

- `latest/article.md`: note投稿バッチ v0.3 に渡すMarkdown
- `latest/article-cover.png`: バッチが自動検出する見出し画像
- `latest/note-post.zip`: 上記2ファイルのまとめ
- `archive/YYYY-MM-DD/`: 過去の生成記事

毎週日曜 06:40 JST に `data/note-weekly.json` を更新し、06:50 JST の通常サイト再生成でこのフォルダも更新する構成です。

公開はこれまでどおり note投稿バッチで下書きを作成し、最終確認後に手動で公開します。
