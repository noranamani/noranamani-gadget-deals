# Codex Cloud automation prompt

このリポジトリの `AGENTS.md` を最初に読み、その指示に従って日次更新を最後まで実行してください。

目的は、楽天市場の商品データを使った「比較 + 特価候補」サイトを毎日更新することです。

必須手順:
1. 最新の main を確認する。
2. `python scripts/fetch_rakuten.py` を実行する。
3. `data/products.json` と `data/history.json` を分析し、`AGENTS.md` の schema と editorial rules に従って `data/editorial.json` を更新する。
4. `python scripts/build_site.py` を実行する。
5. `python scripts/validate.py` を実行する。
6. `git diff` を確認し、根拠のない文章や秘密情報が混入していないか確認する。
7. 変更があれば `bash scripts/publish.sh` を実行する。変更がなければ終了する。

禁止事項:
- APIデータにない仕様を推測しない。
- 特価商品を無理に作らない。
- 秘密値を表示・保存しない。
- テスト失敗を無視して公開しない。

最後の報告は、更新商品数、特価候補数、公開の成否だけを簡潔にまとめてください。
