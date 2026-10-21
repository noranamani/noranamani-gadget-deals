# 楽天API・Windows移行の調査とネットワーク検証

更新日: 2026-10-21（日本時間）

## 現在の結論

社内プロキシを通したPythonのHTTPS通信は成功した。楽天APIへの到達も確認できたが、**Application IDとAccess Keyがローカルで未設定のため、認証付きの商品取得は未確認**である。本番移行はまだ実施できない。

この文書は実施結果と移行案の記録であり、本番移行の承認や完了を意味しない。ドキュメントのGit保存・公開だけが追加で承認されている。

APIキー、トークン、パスワードは記載していない。公開リポジトリでの参照を想定し、社内プロキシのアドレス、出口IPの一部、PC内の絶対パスも省略した。

## 実施範囲と保全

- 調査したmain: `40db906e79ba371e99d20406e662b0e89815ca97`。
- `AGENTS.md`、`README.md`、`SETUP-CODEX.md`、`scripts/`、GitHub Actions、`config/`、note関連処理を調査した。
- 前回作成した `safe_network_probe.py`、`safe_rakuten_smoke.py` と検証結果を再確認した。
- 今回の再検証は2026-10-21 10:28 JSTに実施した。
- API取得用の中継サービスやIP制限の回避手段は利用していない。IP確認サービスは出口IPの観測だけに使用した。
- HTTPS証明書検証とホスト名検証は有効にした。
- 実際の商品データと価格履歴は変更していない。前回の生成テストも `.git` を含まない一時コピーで実施した。
- 商品データのpush、本番公開、Windowsタスク登録・変更、ChatGPTタスク追加、noteへのログイン・投稿は行っていない。
- 今回GitHubに保存する変更は、このドキュメント1ファイルだけである。

再検証前後のSHA-256は一致した。

| ファイル | SHA-256 |
| --- | --- |
| `data/products.json` | `dd00a3dfaed230bfe4513c0818c29f4c10111a13a1cd2ca7b6a214758f22d51b` |
| `data/history.json` | `31775aa3c69222f5626193f8de792c1d57a96887fec39eb4da30002b1723c2f0` |

## 今回の通信・認証検証結果

| 項目 | 結果 | 解釈 |
| --- | --- | --- |
| Pythonが検出したHTTP/HTTPSプロキシ | ユーザー指定の設定と一致 | 明示したProxyHandlerでも同じプロキシを使用 |
| 楽天・IP確認先のプロキシ除外 | すべてfalse | 対象ホストへの通信でプロキシを除外しない設定 |
| IP確認先1 | HTTP 200、TLS検証成功 | 外向きIPを取得 |
| IP確認先2 | HTTP 200、TLS検証成功 | 1と同じIPを取得 |
| 外向きIP | `13.115.98.xxx` | 完全な値はローカルの検証結果だけに保存 |
| 楽天APIへの未認証リクエスト | HTTP 400、TLS検証成功 | APIへの到達確認。認証成功・IP許可を意味しない |
| 認証付きリクエスト | 未実施 | 必須の認証情報が不足していたため送信せず停止 |
| 商品取得件数 | 0件 | 商品が存在しないという判定ではない |
| 本番移行 | 保留 | 認証付きの少量取得と全体検証が必要 |

Python 3.12.4の標準ライブラリ `urllib.request` を使用した。現行の `scripts/fetch_rakuten.py` と同じ通信ライブラリであり、追加パッケージは不要である。

楽天の未認証HTTP 400応答では、確認対象にしたトップレベルのエラー文字列フィールドは取得できなかった。したがって、HTTP 400の具体的な原因をIP拒否やキー不正と断定しない。

Application IDとAccess Keyについて、値を表示せず以下を調べた。

- 実行プロセスの環境変数。
- 通常のWindowsユーザーの環境変数。
- システム環境変数。
- 楽天用に限定した既知の名前のWindows汎用資格情報。

いずれも未設定で、資格情報API自体のアクセスエラーはなかった。他サービスの資格情報や、PC内の無関係なファイルを探索していない。別名の保存先がある場合は、その保存方法・場所だけをユーザーに確認する。

## 楽天の認証方式とIP登録の確認事項

[公式の商品検索API仕様](https://webservice.rakuten.co.jp/documentation/ichiba-item-search)では、現行エンドポイントは次のとおり。

```text
https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701
```

`applicationId` と `accessKey` の組み合わせが必要である。`accessKey` はヘッダーで送信できる。Affiliate IDはAPI仕様では任意であり、認証・少量商品取得の試験には不要である。一方、既存の本体スクリプトは通常実行で3値すべてを必須としている。

[公式ガイド](https://webservice.rakuten.co.jp/guide)では、ID・アクセスキー・アクセス制御はアプリごとに管理される。既存アプリに登録された許可IP、アプリ種別、API scope、およびIP入力形式・件数上限は、ログイン後の実設定の確認が必要である。公開資料だけでは確認できていないため、具体的な上限やCIDR・IPv6対応を推測しない。

IP登録の際は次を確認する。

1. 登録するのは楽天から見える送信元グローバルIP。社内プロキシの内部IPではない。
2. 今回の観測は2接続先で一致したが、固定IPであることや楽天宛てにも同じ出口を使用することまでは証明していない。
3. ネットワーク管理者に楽天宛ての出口IPと固定性を確認する。
4. 楽天の既存アプリの許可IP設定を確認し、必要であればPC側の出口IPを追加する。Oracle復旧に備え、既存Oracle用の設定を不用意に削除しない。
5. 正しいアプリのID・キーを使用し、実際のバックエンド用途に合う設定で試験する。

## ユーザーによる安全な設定と再開手順

キーやトークンをチャットへ貼らず、コマンド引数・ログ・平文の設定ファイルにも書かない。

推奨する今回の設定方法は、同じWindowsユーザーの資格情報マネージャーに2つの汎用資格情報を保存すること。

1. コントロールパネルから「資格情報マネージャー」→「Windows資格情報」→「汎用資格情報の追加」を開く。
2. 下表の2項目をそれぞれ登録する。「パスワード」欄へ、本人が該当する実際の値を入力する。

| インターネットまたはネットワークのアドレス | ユーザー名 | パスワード欄に本人が入力する内容 |
| --- | --- | --- |
| `noranamani-gadget-deals/RAKUTEN_APP_ID` | `RAKUTEN_APP_ID` | 同じ既存アプリのApplication ID |
| `noranamani-gadget-deals/RAKUTEN_ACCESS_KEY` | `RAKUTEN_ACCESS_KEY` | そのアプリのAccess Key |

3. 楽天Web Serviceへ本人がログインし、前節の許可IP・アプリ設定を確認する。
4. チャットには秘密値を送らず、「設定済み」とだけ連絡する。登録画面の条件が不明な場合も、秘密値を除いた条件だけを伝える。
5. 通常のWindowsユーザー環境から、ローカルの試験スクリプトを再実行する。

ローカルの `safe_rakuten_smoke.py` は今回、2つの必須認証情報と上記の汎用資格情報を利用できるよう調整した。`--prompt-secrets` による対話端末での非表示入力も可能だが、値をチャットやCodexのツール入力に渡してはならない。

再開時の試験条件は次のとおり。

- 1リクエスト、先頭カテゴリ、最大3商品に限定する。
- `accessKey` ヘッダーを使い、リダイレクトを追従しない。
- TLS検証を維持し、認証失敗で自動再試行しない。
- HTTPステータスと取得件数を確認する。エラー情報は秘密値を除去して記録する。
- 商品コード・商品名・価格等の必要最小限だけを一時領域に保存し、アフィリエイトURLを保存しない。
- `data/products.json` と `data/history.json` の前後ハッシュを比較する。
- 成功してもpush・公開・定期実行登録へ自動的に進まない。

## 前回までに確認したGitHub通信・認証

- プロキシ経由でGitの `ls-remote` とcloneが成功した。
- 通常のWindowsユーザー環境ではGit Credential Managerの既存資格情報で認証でき、対象リポジトリのpush権限を確認した。
- Codexの制限環境では資格情報取得やschannelが失敗する場合があった。これをPC全体の認証情報不足と混同しない。
- Git既定のOpenSSLと、通常環境でのschannelの両方で、証明書検証を有効にした読み取りを確認した。
- Pages設定は `main` の `/docs` から公開する方式だった。
- Actionsの既定 `GITHUB_TOKEN` 権限はreadだった。

## 前回の生成・既存データ検証

2026-10-21の初回調査では、一時コピーで次が成功した。

- `validate_data.py`、`build_site.py`、`validate.py`。
- `build_note_article.py`、`build_note_manifest.py`、`check_note_bundle.py`。
- 既存unit test 17件。Windows既定cp932ではencoding省略箇所に1件エラーが発生したが、`python -X utf8` では全件成功した。
- 追跡対象71ファイルのハッシュが生成検証前後で一致した。

既存商品データの取得時刻は2026-10-20 06:10 JST。8カテゴリ・掲載144商品、履歴473商品・2,905行を確認した。これは既存データの状態であり、今回のAPI新規取得件数ではない。

## 本番移行案と必要な追加検証

予定する日本時間の役割分担は次のとおり。

| 時刻 | 実行元 | 処理 |
| --- | --- | --- |
| 毎日06:10 | Windowsタスクスケジューラ | 楽天取得・履歴継承・検証・GitHub反映 |
| 毎日06:30 | 既存ChatGPTタスク | `data/editorial.json` 更新 |
| 日曜06:40 | 既存ChatGPTタスク | `data/note-weekly.json` 更新 |
| 毎日06:50 | GitHub Actions | サイト生成・検証・Pages公開、条件を満たしたnoteセット生成 |

今回、実際に登録されたChatGPTタスクの設定は照会していない。タスクは追加せず、移行前に既存枠の時刻・プロンプトを確認する。

### Windows取得処理

- Pythonを直接起動し、毎回Codexを起動しない。`-X utf8` と絶対パスを使用する。
- 資格情報・ログ・運用checkoutはOneDrive同期外のローカル領域を推奨する。
- プロキシと信頼するCAを実行プロセスへ適用する。証明書検証を無効にしない。
- `urllib` にWindowsのワイルドカード式除外設定をそのまま渡さず、内部IP帯・ローカル名の扱いを明示する。
- 最新mainの履歴を読み、一時出力へ全カテゴリを取得する。鮮度・カテゴリ網羅・価格・履歴保全を検証してから採用する。
- 重複起動、06:30を過ぎる遅延更新、push競合を検出し、強制pushしない。
- 本番と同じWindows実行ユーザーで、ログオフ・再起動後の資格情報利用も試験する。

現行 `fetch_rakuten.py` は、壊れた履歴JSONでも空の既定値へ戻るため、履歴喪失の危険がある。本番では破損・不存在を検出して停止する必要がある。また、products/historyを順に別々に保存するため、一時生成・検証・バックアップ・復旧手順を用意する。

`validate_data.py` だけではカテゴリの全落ちや大幅な件数減少、履歴保全までは保証できない。認証後は一時コピーで全カテゴリ取得、既存履歴キーと過去行の維持、部分失敗・429・タイムアウト・競合時の停止も検証する。

### GitHub Actions公開処理

- 06:50 JSTは21:50 UTC。scheduleは遅延や欠落の可能性があるため、定刻完了を保証する前提にしない。
- 最新mainを取得し、当日商品データと、それ以降に編集されたeditorialの整合性を確認する。
- `build_site.py` → `validate.py` を実行する。
- Pagesの公開元をGitHub Actionsへ切り替え、Pages Artifactから同じworkflowで明示的にデプロイする。
- デプロイには `pages: write`、`id-token: write` と `github-pages` environmentを設定する。
- note生成物をcommitする工程だけ `contents: write` を明示する。
- GitHub Actions側に楽天のキーや社内プロキシを設定する必要はない。

[GitHubの公式仕様](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)では、`GITHUB_TOKEN` によるpushだけでは従来のPagesビルドは起動しない。既存の `publish.sh` を移すだけでは不十分である。

### 履歴・カテゴリ・比較ページ・noteへの影響

- 履歴日付は既存実装ではUTC。06:10 JSTの記録が前日UTCの日付となる。移行時に過去の日付を書き換えたり欠測日を捏造したりしない。
- 既存のカテゴリ拡張は日曜05:15 JST、最大1件/14日、上限16、2026-10-25開始。最新 `queries.json` を読めばWindowsでも追加カテゴリを取得できる。
- 商品鮮度36時間や前回追加カテゴリ6固有商品等の条件、`category-expansion-state.json` を維持する。現在のデータが更新されないまま10/25を迎えると、鮮度不足で追加が止まる判定だった。
- 比較ページはJST日付と商品条件で段階公開される。生成時に不成立のページが削除されるため、部分取得のデータで再生成しないことが重要である。
- 既存PCのnote処理は、mainの同じコミットから記事・画像・週次編集JSONを取得する。note生成物をActions Artifactだけに変更すると、この受け渡しが成立しない。
- リポジトリ文書の「手動公開」と別に、PC側には週次自動公開処理が存在する。前回確認では日曜07:10の既存タスクは無効だった。今回は状態を変更していない。
- 06:50開始から07:10まで20分のため、Actionsや週次編集の遅延が影響する。投稿側の当週・当日・3時間以内・重複防止の条件を緩めない。
- 既存note投稿履歴・処理途中状態・ブラウザープロファイルは削除・初期化しない。投稿タスク有効化や投稿時刻変更も別途承認を受ける。

## Oracle復旧時の切り戻し案

1. Windows取得タスクを停止し、実行中処理がないことを確認する。
2. Oracle側で最新mainを取得し、Windows期間中に増えた履歴とカテゴリ状態を継承する。古いVMのデータで上書きしない。
3. Oracleの出口IP、楽天の許可設定、API認証、GitHub認証を少量テストする。
4. 最小の切り戻しは06:10の取得元だけをOracleへ戻し、06:50のActions公開を維持する方法。
5. 旧06:50方式まで戻す場合に限り、Actions公開を止め、最新データから生成・検証してPages公開元と旧生成処理を戻す。
6. 両取得元を同時稼働させない。コード・設定だけを戻し、リポジトリ全体を過去へ強制pushしない。

## 次に再開する作業

**ユーザーによる認証情報と楽天側の設定確認を待っている。** 設定後、最小1回の商品取得を行い、HTTPステータス・認証結果・件数・秘密情報を除いたエラー・データ保全を再報告する。

認証・少量取得に成功しても本番移行は自動承認されない。全カテゴリと失敗時挙動の検証、変更差分のレビュー、ユーザー承認が必要である。

ローカルには日時別の `recheck-results.json` と `rakuten-smoke-result.json` を保存した。完全なネットワーク情報と調査用スクリプトはこのドキュメントのpushに含めない。

## 公式参照資料

- [楽天商品検索API](https://webservice.rakuten.co.jp/documentation/ichiba-item-search)
- [楽天Web Serviceガイド](https://webservice.rakuten.co.jp/guide)
- [Python urllibのプロキシ設定](https://docs.python.org/3.12/library/urllib.request.html)
- [PythonのTLS・信頼ストア](https://docs.python.org/3.12/library/ssl.html)
- [GitのHTTPS・プロキシ設定](https://git-scm.com/docs/git-config)
- [GitHub scheduleの制約](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Pagesのカスタムworkflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
