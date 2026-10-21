# Windows商品取得・Actions公開の移行手順（承認前）

このブランチはレビュー用。main / developへのマージ、運用タスク登録、Pages設定変更は未実施。既存の実行環境・商品データ・投稿状態は変更していない。

## 構成

- 06:10 JST: WindowsタスクがPythonを直接実行し、最新mainを専用bareリポジトリへ取得する。
- 06:30 JST: **既存**ChatGPTタスクが当日の商品世代を参照し、editorial.jsonだけを更新する。
- 日曜06:40 JST: **既存**ChatGPTタスクがnote-weekly.jsonだけを更新する。
- 06:50 JST: Actionsが最新mainの同一スナップショットからサイトを生成・検証し、Pages Artifactを明示デプロイする。日曜のみnoteセットをmainに保存する。note投稿は実施しない。

Windowsは3資格情報をCredReadWで読み、Application ID / Affiliate IDをAPI要求、Access Keyをヘッダーに使用する。認証値の環境変数・平文ファイルへの代替はない。外部通信に明示プロキシを使用し、TLS/ホスト名検証を維持する。CA追加は既存信頼ストアへの追加であり、検証の無効化ではない。

## 取得・保存・競合の停止条件

config/queries.jsonを毎回動的に読み、1カテゴリ2リクエスト、各最大30商品、開始間隔1.3秒以上、再試行なし。現行8カテゴリは16リクエスト、上限64カテゴリ。API規約の1アプリ毎秒1回以下に加え、同じApplication IDを使う別処理も重複実行しない。

履歴の不存在・破損・不正JSON（重複キー/NaN含む）・不正価格/日付で停止する。過去全キーと全行の一致を検証し、欠測日は埋めず、UTC日付を継承する。120日での切り捨ては行わない。同じUTC日の同価格は再実行可能、異なる価格は上書きせず停止する。異なるカテゴリで同じ商品コードの価格が矛盾しても停止する。

各カテゴリの掲載数が max(6, 前回掲載数の半分を切上げ) 未満、部分失敗、無効なAPI返却アフィリエイトURL、HTTP異常、タイムアウトで世代を採用しない。取得数の下限はレビューで調整可能だが、停止を自動的に無視しない。

products/history/collection-statusのJSONを検証・fsyncし、元データのハッシュ付きバックアップを確認してから世代ディレクトリを完成させる。CURRENT.jsonの単一置換でローカル読取世代を切り替える。Windowsの電源断時のディスク耐久性までは保証しない。失敗時の未採用ディレクトリは調査用に残す。

Gitにはこの3ファイルだけを単一コミットとして作成する。専用インデックスを使用し、運用checkoutや既存インデックスをresetしない。開始時mainに変更があれば停止し、通常の非force pushでも競合を拒否する。通信途絶時は成否不明として停止し、次回にリモートを確認する。rebase・force push・自動再pushは行わない。

## 本番移行後の個人用設定（今回実行しない）

Python 3.12とGit for Windowsを使用する。コード配置と実行状態はOneDrive同期外、例としてLOCALAPPDATA配下に分離する。タスクの実行ユーザーだけがアクセスできるACLを確認し、コード・CA・設定への第三者の書込を許可しない。

設定例はoperations/windows-pipeline.example.json。これをリポジトリ外のpipeline-local.jsonへコピーし、実際のプロキシURL等を**ローカルだけ**で設定する。プロキシ認証情報はURLに含められない。ca_bundleは不要ならnull。Gitは同じプロキシとCAで証明書検証を有効にする。Pythonのローカル除外は従来のローカル名・ループバック・指定プライベートサブネットに対応し、外部NO_PROXY指定があれば直接通信せず停止する。

Windows汎用資格情報の登録先は次の3件。同じ通常ユーザーで登録する。

- noranamani-gadget-deals/RAKUTEN_APP_ID
- noranamani-gadget-deals/RAKUTEN_ACCESS_KEY
- noranamani-gadget-deals/RAKUTEN_AFFILIATE_ID

以下のコマンドは移行承認後の手順例。キーやIDの値は引数へ渡さない。

```powershell
& 'C:\Python312\python.exe' -X utf8 'C:\GadgetPipeline\scripts\windows_credentials.py' --check
& 'C:\Python312\python.exe' -X utf8 'C:\GadgetPipeline\scripts\windows_credentials.py' --verify-affiliate "$env:LOCALAPPDATA\GadgetPipeline\affiliate-proof.json"
& 'C:\Python312\python.exe' -X utf8 'C:\GadgetPipeline\scripts\run_windows_update.py' --config "$env:LOCALAPPDATA\GadgetPipeline\pipeline-local.json"
```

最後のコマンドはプレビューだが**実APIを最大2×カテゴリ数回呼ぶ**。今回のオフラインテストの代わりに繰り返し実行しない。--applyなしではコミット作成・pushはしない。適用にはローカルenabled=trueと--applyの両方が必要。

### アフィリエイト帰属の確認

楽天公式FAQでは、API入力IDと返却URL内IDが異なる場合でも、同じ楽天会員へ紐付いていると説明されている。返却リンクに登録IDが文字列として含まれないことだけでは誤帰属と断定できない。

本人が対象の楽天会員で[公式アフィリエイトID確認画面](https://webservice.rakuten.co.jp/account_affiliate_id/)へログインし、対象アプリと登録IDが正しいことを確認する。--verify-affiliateはその画面のIDを非表示入力で資格情報のAffiliate IDと照合する。値そのものは保存せず、Application IDとAffiliate IDに結び付いたローカル確認記録を保存する。確認記録は30日で失効し、ID変更・失効時は再確認する。この記録は自己申告の補助で、楽天の成果報酬実績を保証するものではない。

本人によるDevelopers登録IDとWindows登録IDの一致確認に基づくローカル記録を作成済み。30日間の期限と資格情報への結び付きを毎回検証する。これは本人の確認記録であり、楽天の成果報酬実績の確認ではない。**本番公開は保留**。コードはAPIが返したHTTPSの楽天アフィリエイトURLをそのまま使い、商品コードから販売URLを組み立てない。確認記録が無効ならプレビューだけが可能で、適用と公開は停止する。

公式資料: [API応答URLのIDが異なる場合](https://webservice.faq.rakuten.net/hc/ja/articles/900001970686)、[入力IDとリンクIDについて](https://webservice.faq.rakuten.net/hc/ja/articles/900001970666)、[商品検索API](https://webservice.rakuten.co.jp/documentation/ichiba-item-search)。

### 返却URLと秘密情報の区別

公式商品検索仕様では、affiliateId指定時にaffiliateUrlが返り、itemUrlも同じリンクになる。保存済み商品URL144件では、Application IDがrafcidパラメーターの所定位置に入っていた。これは保存データで観測した互換条件であり、公式仕様に記載された将来不変の書式とは扱わない。

OutputPolicyは、認証済みAPIクライアントが応答から検証したURLの原文だけを、その取得世代のproducts内urlフィールドに許可する。HTTPS・楽天の正規ホスト・認証情報なしのauthority・追跡パス・重複なしの既知パラメーターを確認し、Application IDはrafcidの観測済み完全一致位置だけ、Affiliate IDは完全一致する追跡パスだけを検査用コピー上で区別する。該当時は遷移先pcが楽天商品ホストのHTTPSであることも要求する。任意のURLや任意のパラメーターへの例外ではない。

Access Keyは全位置で拒否する。Application ID/Affiliate IDも上記以外の位置、商品名、ショップ名、画像、履歴、状態情報では拒否する。パーセント・HTML文字参照・Unicodeエスケープの多重表現も検査する。itemUrlはaffiliateUrlと原文一致する場合だけ同じ検査を受ける。返却URLを組み立て直したり書換えたりしない。未知の形式・不正な遷移先・秘密値混入では停止する。

API応答本文・URL・登録値はログへ出さない。エラーはコード側の固定ラベル（資格情報の種類・検査処理・既知のフィールド名）だけとする。汎用秘密値検査は維持する。今回の修正・文書に実値や実URLを追加しない。将来の承認済み商品更新では、公開用の正規追跡URLを商品リンクとして保持する設計なので、その公開範囲もレビュー対象とする。

### タスクスケジューラ（未登録）

実行プログラム: Pythonの絶対パス。引数: -X utf8 とラッパー・個人用設定の絶対パス、承認後のみ--apply。06:10 JSTを想定する。通常ユーザーと同じアカウントで「ユーザーがログオンしているときのみ実行する」（InteractiveToken）を選ぶ。管理者昇格は通常不要。PASSWORD方式・S4U方式・パスワード入力は不要。PC常時起動・当該ユーザーのログイン維持を運用条件とする。

起動06:10〜06:20、取得は06:25まで、push開始は90秒の通信枠を残せる06:23:30までに制限する。通信中の切断・時刻変更で完了が不明な場合はリモートを確認する。遅延起動・日付跨ぎは停止する。「新しいインスタンスを開始しない」を指定し、アプリ側でも排他ロックを使用する。自動再試行や、実行漏れを夜間に無条件実行する設定は避ける。PC時刻・タイムゾーンを確認する。残ったロックはプロセス不存在を本人が確認してから解除し、自動削除しない。

Git Credential Managerの同じユーザーの資格情報を使用する。必要なら対象リポジトリ限定、Contents read/writeのfine-grained PATをGCMへ本人が登録する。コードやログにはトークンを書かない。保護ブランチが直接pushを拒否する場合は権限迂回せず運用方針を再検討する。

承認済み一時InteractiveTokenタスクで3資格情報・有効な本人確認記録・プロキシHTTPS/TLS検証・GitHub/GCM非対話読取を確認済み。一時タスクは削除済み。画面ロック試験はRDP利用のため本人希望で省略しており、RDP切断中の動作も未確認。ログオフ・再起動後の無人実行は今回の運用対象外。画面ロックとログオフは区別する。画面ロック試験を再度行う場合は本人操作を待つ。

ユーザーはAC電源時20分の自動スリープを解除済みと報告している。今回、電源設定の変更は行わない。ログインを維持してもPCがスリープ・休止・停止していれば通常の予定実行はできないため、将来の設定変更やWindows更新後も常時起動条件を確認する。WakeToRunは今回設定・検証していない。ロック・世代・バックアップは保持するので容量監視と保持期間の運用が必要。

[Microsoftのログオン方式](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_logon_type)

## 編集タスクの既存プロンプト更新案

新規タスクは追加しない。既存の日次枠をcodex/daily-automation-prompt.mdへ、既存の週次枠をcodex/weekly-note-editorial-prompt.mdへ合わせる。今回はファイル上の提案のみで、登録済みタスクは変更していない。

両JSONへsourceProductsSha256を追加し、同じmainコミットのcollection-status.jsonにあるproductsSha256を転記する。生成時刻は実編集時刻を使う。数値・時刻・ハッシュを捏造せず、元データ未更新なら編集しない。日次は取得以降・当日、週次は当週日曜・日次編集以降を要求する。カテゴリは現在の全IDを扱う。旧プロンプトのHTML生成・publish.sh実行は取り除く。

## Actions公開（初期無効）

windows-daily-publish.ymlは毎日21:50 UTCに予定されるが、mainでRepository Variable WINDOWS_PIPELINE_ENABLED=trueの場合だけ動作する。今回このVariable、Pages設定、environmentは変更していない。feature/developでは実デプロイしない。

準備jobはContents read、note保存jobだけContents write、デプロイjobだけContents read / Pages write / ID token writeを持つ。サードパーティActionは使わず公式ActionをコミットSHAで固定した。楽天資格情報・社内CA・社内プロキシはActionsへ渡さない。オフラインCIはWindows/Ubuntu、Contents readだけで動く。

当日06:10以降・3時間以内の商品、同じ商品ハッシュの日次編集、完全カテゴリ、アフィリエイト確認を検査する。日曜は当週のnote編集と同じ商品ハッシュを確認して記事・PNG・ZIP・manifestを一時生成し、note/latestの4ファイルと当日archiveの2ファイルだけをmainへ保存する。平日は先週日曜の整合するセットを再利用して書き換えない。既存のnote投稿処理は同じmainコミットのJSON・記事・画像を読める。記事の投稿や既存投稿タスクの有効化は行わない。

成果物をハッシュ検証し、30分以内だけ使用する。準備後mainが進んだ場合は停止する。Pages直前にもmainを確認するが、確認からデプロイまでの微小な競合窓は残る。次の日の編集がその間に走らない時刻設計と監視で対処する。

Pagesの公開元を「GitHub Actions」へ変更し、github-pages environmentの対象をmainへ制限する操作が承認後に必要。GITHUB_TOKENのpushだけでは従来のbranch方式Pagesが起動しないため、直接デプロイする。

ビルド・検証・note保存に失敗すればPagesへ進まない。Pagesデプロイ失敗時は従来の公開サイトを維持する。noteのGitコミットとPagesデプロイはサービスを跨ぐため原子的ではなく、Pages失敗時にもmainのnoteセットだけ先に更新済みとなる場合がある。07:10の投稿枠は従来どおり無効を維持し、初回移行確認と別途承認まで有効化しない。Actions scheduleは遅延・欠落の可能性があり、06:50完了は保証されない。停止時はActions結果と固定エラーコードを確認する。

[GitHub Pagesカスタムworkflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)

## テスト・レビュー

```powershell
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```

回帰テストは外部API・実資格情報・GitHub書込を使わず、一時ディレクトリとローカルbare Gitで正常系・異常系を検証する。生成日時やリンクを補ったテストデータはフィクスチャ限定で、本番に採用しない。別途の保存URL再生は、実資格情報をメモリ内だけで照合し、URL・値を出力せず一時環境で行う。証明書の実ハンドシェイクやGitHub Pages環境はオフライン試験では検証されない。修正後の実API試験を追加する場合は、理由と回数を示し事前承認を得る。

## 承認後の移行順序と切り戻し

## 初回移行の互換性と順序（承認後だけ実施）

現行mainにはcollection-status.json、日次/週次のsourceProductsSha256、note/latest/manifest.jsonがない。旧JSONへ現在のハッシュや新しいgeneratedAtを機械的に埋めて整合したように扱ってはならない。取得・編集が成功して初めて新形式を採用する。未更新なら公開を停止し、直前の公開サイトを維持する。

### 推奨: 日曜日から開始

1. 独立バックアップと復元検証を済ませ、PRのdevelop取り込み・mainリリースを別途承認する。本番用ローカルenabledとRepository Variableは、承認した切替順序まで無効のままにする。
2. 既存Oracle処理の停止を確認する。既存の日次/週次編集タスクは、同じmainの商品世代を読みsourceProductsSha256を記録する提示済みプロンプトへ、別途承認して更新する。新しいタスクは作らない。
3. 06:10のWindows取得でproducts/history/statusを単一コミットとして作成する。欠測日を埋めない。06:30の日次編集はその世代だけを参照する。日曜06:40の週次編集も同じ商品世代と日次編集以降の実時刻を使う。
4. 06:50の生成は新しい週次JSONから記事・画像・ZIP・manifestを作り、schema_version=1の共通契約を維持する。旧noteのmanifestがなくても、当日の週次編集が有効なら新規生成できる。元JSONや日付を埋め合わせる必要はない。
5. 初回の一時生成・差分・履歴件数を監視し、承認したPages設定・有効化を最後に適用する。note実投稿は別途承認まで停止したままとする。

### 平日開始を選ぶ場合: 旧noteを明示的に固定

平日は既存noteを新価格で作り直さない。直近の日曜の週・生成日が確認できる既存記事/画像/ZIPを持つ場合だけ、移行承認後に次の準備を行える。古い週のままなら停止し、日曜開始を選ぶ。週や生成日を現在へ書き換えない。

```powershell
# 移行承認後の例。現在の最新mainを置いた専用checkoutを指定する。
$sourceCommit = git -C 'C:\GadgetPipeline\checkout' rev-parse HEAD
& 'C:\Python312\python.exe' -X utf8 'C:\GadgetPipeline\scripts\prepare_legacy_note.py' --root 'C:\GadgetPipeline\checkout' --source-commit $sourceCommit --output "$env:LOCALAPPDATA\GadgetPipeline\legacy-note-manifest.json"
```

このコマンドはリポジトリ外へmanifestを準備するだけで、Gitへの書込み・push・投稿・公開は行わない。入力4ファイルが指定コミットと一致することをGitフィルター込みで確認し、Git archiveで展開した原本のハッシュを記録する。WindowsのCRLFとGitのLFを混同しない。ZIPと単体Markdownの比較だけはCRLF/LF同値を認め、原本のファイルとZIPはそれぞれ変更せず別々にハッシュで固定する。画像は完全一致を要求する。

manifestはsource_binding=legacy-preserved-unbound、元コミット、元週次JSONのハッシュ、記事/画像/ZIPのハッシュを持つ。sourceProductsSha256は捏造せず、旧noteと現在の商品の対応を確認済みと称さない。schema_version=1、媒体、issue_key、generated_at、既存ファイル名は維持する。

本人が差分を確認し別途承認した後だけ、このmanifestをnote/latest/manifest.jsonへ採用して通常commit・pushする。旧記事・画像・ZIP・週次JSON・投稿台帳は変更しない。初回06:10取得と06:30編集が新形式を満たせば、06:50にサイトだけを生成し、旧noteは保存物として保持する。平日の公開処理はnoteをコミットしない。次の日曜は06:40の新しい編集を必須とし、旧note固定を理由に迂回しない。

### 継続運用の停止条件とnote互換性

- 日次編集は当日06:30以降、取得以降、公開準備以前、同じ商品ハッシュ・全カテゴリを要求する。日次の遅延・失敗で旧JSONが残れば停止する。
- 日曜の週次編集は当日06:40以降、日次編集以降、準備以前、同じ商品ハッシュを要求する。未更新・ハッシュ欠落・前週のままなら停止する。
- 新しい週次manifestはsource_binding=products-sha256、商品ハッシュ、週次JSONのハッシュ、記事/画像/ZIPのハッシュを保持する。平日はその一式を再検証し、破損や入替えがあれば公開へ進まない。
- 共通note契約のschema_version=1、site_id、issue_key、status、title、article_path、cover_path、source_url、generated_atを維持する。既存check_note_bundle.pyとplan_note_queue.pyは追加フィールドを許容する。実投稿バッチの外部環境による受入れ試験は未実施で、実投稿・台帳更新は行わない。
- GitHub Actionsのscheduleは遅延・欠落し得るため、06:50完了を保証しない。固定エラーコード・世代時刻・Actions結果を監視し、失敗時に時刻やハッシュを補完しない。[公式schedule仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 切り替え操作と切り戻し

1. PRをレビューしdevelopへの取り込みを判断する。本番main取り込みも別途承認する。
2. 既存Oracleの両処理が停止していることを確認する。認証・帰属・CA・GCM・既存編集タスクの設定を確認する。
3. 専用ローカル環境でログオン時限定のプレビューを承認のうえ確認する。実APIへの追加通信は理由と回数を示し別途承認を得る。ログオフ状態の試験は行わない。
4. Pagesの公開元・environment・有効化Variable、Windows個人用enabled、06:10タスクを承認した順序で切り替える。初回は手動監視し、最新mainの履歴件数とPages成功を確認する。
5. note投稿の有効化や時刻変更は、この移行とは別に承認する。

停止はWindowsタスクと個人用enabled、ActionsのWINDOWS_PIPELINE_ENABLEDを無効にする。実行中処理の終了を確認する。サイトは直前の成功デプロイを維持し、データ・履歴・投稿台帳を削除しない。

旧Oracle用シェル3本はこのコードでは誤実行防止のため即時停止する。旧fetch_rakuten.pyの引数なし実行も停止する。Oracleが復旧しても古い120日切捨て・破損時初期化の取得処理をそのまま再開しない。最新mainの履歴を取得し、この履歴保全ロジックを使うLinux用認証アダプターを別途実装・検証してから、取得元だけOracleへ戻す。Actionsは維持できる。完全な旧方式への切り戻しは別途Pages設定と生成処理の検証が必要。過去コミットへリポジトリ全体をreset/force pushして履歴を巻き戻してはならない。
