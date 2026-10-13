# Codex Cloud Automation 設定メモ

## 構成
- Oracle VM: 毎日 06:10（Asia/Tokyo）に楽天APIから商品データを取得し、基本サイトを生成してmainへpush
- Codex Cloud: 毎日 06:30（Asia/Tokyo）にmainの最新データを読み、比較コメントを更新して再生成・公開

楽天APIは許可IPをOracle VMの固定IPに限定しているため、Codex Cloudからは直接呼びません。

## 推奨スケジュール
毎日 06:30（Asia/Tokyo）

## Automation prompt
`codex/daily-automation-prompt.md` の内容をそのまま使います。

## Cloud environment
- 対象リポジトリ: `noranamani/noranamani-gadget-deals`
- GitHub: Read/Write可能な接続を使用
- 楽天のApplication ID / Access Key / Affiliate IDは設定しない
- 追加のGitHubトークンも、接続済みGitHubでpushできる場合は不要

## 初回だけ確認する点
1. 最新mainに `data/products.json` と `data/history.json` が存在する
2. Codexが `data/editorial.json` をschemaどおりに更新する
3. 公開文面にCodex/OpenAI/AI等の生成手段を示す文言が入っていない
4. `python scripts/build_site.py` が成功
5. `python scripts/validate.py` が `validation ok`
6. `bash scripts/publish.sh` でmainへpush
7. GitHub Pagesが更新

## 失敗時
- 商品データが古い: Oracle VM側の `noranamani-gadget-deals.service` のログを確認
- push失敗: Codex Cloud環境のGitHub接続とリポジトリWrite権限を確認
- Pages 404: Settings > Pages の branch `main` / folder `/docs` を確認
