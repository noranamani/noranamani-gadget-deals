# noranamani GADGET DEALS v2 — Codex Cloud完全自動版

楽天市場の商品を「比較 + 価格履歴 + 特価候補 + Codexの短い解説」で毎日自動更新する静的サイトです。

## 追加費用0円で始める構成
- Codex Business: 既契約の利用枠
- GitHub public repository: 無料
- GitHub Pages: 無料
- Rakuten Web Service: 無料
- 独自ドメイン: 不要
- OpenAI API: **使いません**

> Codexの契約自体を0円と数えるのではなく、「既にBusinessを契約済みなので、この仕組みのための追加API課金・サーバー代を発生させない」という意味です。

## 仕組み

毎日1回、Codex Cloud automation が次を行います。

1. 楽天APIから各カテゴリを2種類の並び順で取得
2. 価格履歴を更新
3. 比較スコア・値下げ率・特価候補を計算
4. Codexが当日の短い比較コメントを `data/editorial.json` に作成
5. 静的サイト `docs/` を再生成
6. 検証
7. mainへpush
8. GitHub Pagesが自動公開

PCを常時起動しておく前提ではありません。Codex Cloud環境で実行してください。

## ファイル
- `AGENTS.md`: Codexが毎回守る運用ポリシー
- `codex/daily-automation-prompt.md`: Automationへ貼るプロンプト
- `scripts/fetch_rakuten.py`: API取得・履歴・特価判定
- `scripts/build_site.py`: HTML生成
- `scripts/validate.py`: schema・漏洩・表示必須項目チェック
- `scripts/publish.sh`: 検証後のmain push
- `config/queries.json`: 監視カテゴリ
- `config/site.json`: サイト設定
- `data/history.json`: 価格履歴
- `data/products.json`: 最新商品データ
- `data/editorial.json`: Codexが毎日書く比較コメント
- `docs/`: GitHub Pages公開物

## セットアップ 1 — 楽天Web Service
Rakuten Web Serviceでアプリを登録し、以下を取得します。
- Application ID
- Access Key
- Affiliate ID

## セットアップ 2 — GitHub
1. public repository を作成。例: `noranamani-gadget-deals`
2. このZIPの中身をリポジトリ直下へ入れてmainへpush。
3. Settings > Pages で `Deploy from a branch` を選択。
4. Branch `main`, Folder `/docs` を選択。

## セットアップ 3 — GitHub write token
Codex Cloudから完全自動publishするため、GitHub fine-grained personal access tokenを1つ作成します。

推奨権限:
- 対象リポジトリ: このリポジトリだけ
- Repository permissions > Contents: Read and write
- それ以外: 原則不要

Tokenはリポジトリへ保存せず、Codex Cloud Environmentのsecret `GITHUB_WRITE_TOKEN` に入れます。

## セットアップ 4 — Codex Cloud Environment
対象GitHubリポジトリを接続してCloud Environmentを作成します。

Secrets:
- `RAKUTEN_APP_ID`
- `RAKUTEN_ACCESS_KEY`
- `RAKUTEN_AFFILIATE_ID`
- `GITHUB_WRITE_TOKEN`

Network accessで最低限、以下へのHTTPSアクセスを許可します。
- `openapi.rakuten.co.jp`
- `github.com`

Python 3.11+ と git が使えれば追加パッケージは不要です。

## セットアップ 5 — 初回確認
Codex Cloudで一度だけ手動タスクを実行します。

```text
AGENTS.mdを読んで日次更新を一度テスト実行してください。公開前にvalidateまで行い、問題なければpublishしてください。
```

成功後、GitHub Pagesを確認します。

## セットアップ 6 — Automation
CodexのAutomationで、このリポジトリのCloud Environmentを選び、`codex/daily-automation-prompt.md` の内容を貼ります。

推奨: 毎日 07:20 Asia/Tokyo

最初の1週間は実行結果を確認し、安定後は基本放置運用にします。

## 特価判定
- 最大120日分の価格履歴
- 最低5回の記録がたまるまで値下げ判定しない
- 直近履歴中央値より10%以上安い: 特価候補
- 20%以上安い: 大幅値下げ候補
- 新安値でも値下げ率が小さい場合は原則「特価」にしない
- 市場全体の最安値を意味しない

## 比較スコア
参考値として次を組み合わせます。
- レビュー平均: 最大30
- レビュー件数: 最大20
- 同一検索内の相対価格: 最大20
- 送料無料: 5
- ポイント倍率: 最大5
- 自サイト価格履歴上の値下げ: 最大20

## カテゴリを変える
`config/queries.json` を編集します。検索語と価格範囲を増減できます。

## 重要な運用方針
- AIに商品スペックを想像させない
- 特価がない日は「なし」でよい
- アフィリエイト表記を常時表示
- 秘密値をHTML/JSONへ出さない
- 自動生成コンテンツを量産するのではなく、価格比較ページを継続更新する

## `site_url`
GitHub Pages URLが確定したら `config/site.json` の `site_url` に入れてください。sitemapが生成されます。
