# Codex Cloud Automation 設定メモ

## 推奨スケジュール
毎日 07:20（Asia/Tokyo）

## Automation prompt
`codex/daily-automation-prompt.md` をそのまま貼り付けます。

## Cloud environment secrets
| Name | 内容 |
|---|---|
| RAKUTEN_APP_ID | Rakuten Web Service Application ID |
| RAKUTEN_ACCESS_KEY | Rakuten Web Service Access Key |
| RAKUTEN_AFFILIATE_ID | 楽天アフィリエイトID |
| GITHUB_WRITE_TOKEN | このリポジトリだけContents writeを許したfine-grained PAT |

## Network allowlist
- openapi.rakuten.co.jp
- github.com

## 初回だけ確認する点
1. `python scripts/fetch_rakuten.py` が成功
2. `data/products.json` に商品が入る
3. Codexが `data/editorial.json` をschemaどおりに更新
4. `python scripts/build_site.py` が成功
5. `python scripts/validate.py` が `validation ok`
6. `bash scripts/publish.sh` でmainへpush
7. GitHub Pagesが更新

## 失敗時
- API 401/403: App ID / Access Keyを確認
- affiliate URLが空: Affiliate IDを確認
- push失敗: fine-grained PATの対象repoとContents権限を確認
- Pages 404: Settings > Pages の branch `main` / folder `/docs` を確認
