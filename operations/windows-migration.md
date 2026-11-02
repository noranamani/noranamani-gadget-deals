# Windows商品取得・Actions公開の移行手順

2026-10-23時点: Windows取得コードはmain反映済みで09:24 JSTの取得・履歴保全・pushに成功。通常06:10タスクは10月25日から有効。Pagesはまだmain/docs方式で、WINDOWS_PIPELINE_ENABLEDは未登録。以下の公開ハードニングはレビュー用で、本番設定変更・実公開・note投稿は未実施。実行成功と実装/設定完了を区別する。

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

入力スナップショットはGitの改行変換を無効にして展開し、全ファイルの内容を元コミットのtree/blobと照合する。Windowsのautocrlf設定で商品・履歴・編集データのハッシュ基準を変えない。export-substやexport-ignore等で原本から変更・欠落する場合も停止する。Windowsの既存checkoutがCRLFなら、そのSHA-256とGit原本（LF）のSHA-256は区別して記録する。バックアップの復元では、そのバックアップに付属するmanifestのバイト単位ハッシュを使う。

## 本番移行後の個人用設定（今回実行しない）

Python 3.12とGit for Windowsを使用する。コード配置と実行状態はOneDrive/AppData外の%USERPROFILE%\\GadgetPipeline\\windows-productionに分離する。LOCALAPPDATAはCodexのMSIX仮想化によりタスクから実体が見えなかったため再利用しない。タスクの実行ユーザー、SYSTEM、AdministratorsだけがアクセスできるACLを確認し、コード・CA・設定への第三者の書込を許可しない。

設定例はoperations/windows-pipeline.example.json。これをリポジトリ外のpipeline-local.jsonへコピーし、実際のプロキシURL等を**ローカルだけ**で設定する。プロキシ認証情報はURLに含められない。ca_bundleは不要ならnull。Gitは同じプロキシとCAで証明書検証を有効にする。Pythonのローカル除外は従来のローカル名・ループバック・指定プライベートサブネットに対応し、外部NO_PROXY指定があれば直接通信せず停止する。

Windows汎用資格情報の登録先は次の3件。同じ通常ユーザーで登録する。

- noranamani-gadget-deals/RAKUTEN_APP_ID
- noranamani-gadget-deals/RAKUTEN_ACCESS_KEY
- noranamani-gadget-deals/RAKUTEN_AFFILIATE_ID

以下のコマンドは移行承認後の手順例。キーやIDの値は引数へ渡さない。

```powershell
$pipelineHome = Join-Path $env:USERPROFILE 'GadgetPipeline\windows-production'
$source = Get-Content (Join-Path $pipelineHome 'code-source.json') -Raw | ConvertFrom-Json
$pipelineScripts = Join-Path $pipelineHome ('code\' + $source.sourceCommit + '\scripts')
& 'C:\Python312\python.exe' -X utf8 (Join-Path $pipelineScripts 'windows_credentials.py') --check
& 'C:\Python312\python.exe' -X utf8 (Join-Path $pipelineScripts 'windows_credentials.py') --verify-affiliate (Join-Path $pipelineHome 'state\affiliate-proof.json')
& 'C:\Python312\python.exe' -X utf8 (Join-Path $pipelineScripts 'run_windows_update.py') --config (Join-Path $pipelineHome 'pipeline-local.json')
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

### タスクスケジューラ（登録済み・ログオン中限定）

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

windows-daily-publish.ymlは毎日06:50 JSTと補完07:15/07:45/08:15 JSTに予定されるが、mainでRepository Variable WINDOWS_PIPELINE_ENABLED=trueの場合だけ動作する。今回このVariable、Pages設定、environmentは変更していない。作業ブランチ/developでは実デプロイしない。

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

移行当初はcollection-status.json、日次/週次のsourceProductsSha256、note/latest/manifest.jsonがなかった。10月23日に取得世代、対応する日次編集、平日用の旧note manifestまでmain反映済み。週次は10月25日の実編集が必要。旧JSONへ現在のハッシュや新しいgeneratedAtを機械的に埋めて整合したように扱ってはならない。未更新なら公開を停止し、直前の公開サイトを維持する。

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
& 'C:\Python312\python.exe' -X utf8 'C:\GadgetPipeline\scripts\prepare_legacy_note.py' --root 'C:\GadgetPipeline\checkout' --source-commit $sourceCommit --output "$env:USERPROFILE\GadgetPipeline\legacy-note-manifest.json"
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

## 2026-10-25向け公開ハードニング（レビュー後にmain反映）

### 遅延・欠落・再実行

06:50、07:15、07:45、08:15 JSTの各checkは最新mainを読む。06:50より早い実行を拒否し、商品3時間、当日06:10以降、日次06:30以降、日曜週次06:40以降、編集順序・全カテゴリ・ハッシュ結合を維持する。前の枠で週次が未完成なら失敗し、例えば07:20に正規編集が完了した場合は07:45枠で新たにprepareできる。編集の生成時刻を遡及・補完しない。

publication_gate.pyはActions APIを読み取り、当日mainの同じworkflowの全attemptのdeploy jobにあるPublish Pagesステップを確認する。成功済みならその日の再公開をスキップする。未試行なら公開候補とし、API読取不可・不正・上限超過は停止する。古い日のworkflowの再実行は拒否し、当日のRun workflowを使用する。workflow全体のconcurrencyで直列化し、deploy直前にも再照合する。

失敗・取消・進行中のPages送信は、サーバー側だけ成功した可能性を排除できないためPUBLICATION_DEPLOYMENT_UNCERTAINで停止する。通信結果不明時の自動再送はしない。API送信前のprepare/検証失敗は補完枠で復帰可能。送信失敗時はPages/Environmentの実結果を確認し、成功済みなら再公開しない。失敗が確定し、当日・3時間以内なら、個別承認した復旧を別途行う。成功履歴や失敗ログの削除で検知を迂回しない。

日曜にnote保存後Pagesが失敗した場合、次のprepareは同じ週・商品ハッシュ・週次JSONハッシュ・本文/PNG/ZIP・archiveの一致を要求して既存セットをそのまま使う。単一コミットが不要ならpushもしない。同週の既存記事を別世代で上書きせず、矛盾は停止する。平日は旧noteを再生成しない。note保存とPagesは原子的ではないため、noteのGit保存をサイト公開成功・note実投稿成功とは扱わない。

### 初回カテゴリ拡張

config/category-expansion.jsonのfirst_addition_jstだけを2026-11-01へ延期する。enabled=true、14日間隔、上限16、既存カテゴリ収集状況の品質条件は維持する。10月25日は追加なし。以後も遅延したapply/pushは日曜05:00〜06:00 JST以外で停止し、push前にremote mainが取得元と同じことを確認する。06:10の商品取得と遅延拡張を競合させない。通常の非force pushで競合を拒否する。

### 失敗検知・通知の限界

公開workflowのreport jobはprepare/note/deployの結果をJob Summaryと固定エラーで示し、失敗を成功として扱わない。Windows取得またはChatGPT編集の欠落・遅延も厳格な世代検査で検知する。GitHubアカウントのSettings → Notifications → ActionsでEmail/Webおよび失敗時のみの通知を選ぶ。scheduled workflowの通知先は最初の作成者、または最後にcronを変更/再有効化したユーザーとなるため、今回のcron変更を取り込む本人が通知の受信対象とメール/アプリ設定を確認する。リポジトリのWatchだけで受信できるとは仮定しない。[GitHub公式の通知仕様](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)。こちらからメール等の外部送信・新しいChatGPTタスク作成は行わない。

GitHubのschedule自体が全枠欠落した場合、その同じworkflowだけでは検知できない。独立監視の設置は別途承認・設計が必要。ChatGPT側の06:30日次/日曜06:40週次の実行はCodexから保証できず、登録済み週次タスクの実設定も本検証からは確定できない。既存タスクの入力条件をcodex/weekly-note-editorial-prompt.mdと照合する。既存日次は本人の更新済み申告を引き継ぐ。

### Windowsの事前診断（10月23日）

同じ通常ユーザー/InteractiveTokenの一時診断タスクを手動起動し終了0、直後に削除した。本番タスクは変更していない。資格情報3件、本人確認の30日期限（11月20日14:37 JSTまで）、明示プロキシ、証明書/ホスト名検証、GCM非対話認証、認証付きGitHub読取HTTP 200とpush権限を確認した。

通信・子プロセスを遮断した区間で本番run_updateを実時計で呼び、時間外ガードで停止した。通信試行0・子プロセス試行0、楽天API要求0。排他ロックの二重取得を一時領域で拒否し、本番の残存ロックなし。配置8モジュールのハッシュと、通常ユーザーから開いた設定ファイルのWindows最終実体パスが論理パスと一致し、AppData仮想化の再発なし。

次回は2026-10-25 06:10 JST、タスクenabled=true・ローカルenabled=true、通常ユーザーのログオン中のみ、Python直接実行、IgnoreNew、自動再試行0、遅延実行なし。既存212件の定義を前後比較し変更0、残存の新規タスク0。

AC時のスリープ/休止タイマーは0（無効）、バッテリー時のスリープは600秒。更新による再起動要求は検出されなかったが、ログオン中の自動再起動を禁止する明示ポリシーは未設定だった。RDP切断後の自動ログオフ制限も参照したローカルポリシーでは未設定。RDP切断中・画面ロック中の実動作、ドメイン/管理ポリシー、将来の更新再起動まで保証しない。AC給電・ユーザーログオン維持・予定時刻前後の更新再起動回避が必要。ログオフ/再起動でセッションを失った場合は本人の再ログオンが必要で、追いかけ取得は行わない。電源/Windows Update/RDP設定は変更していない。

### GitHub本番設定と承認後の順序

10月23日のAPI参照では、Pages build_type=legacy（main/docs）、公開Variableは404（未登録）、Actions enabled=true、workflow既定token権限read。github-pages environmentはcustom branch policyでmainだけを許可、必須reviewer/待機時間なし。workflowはactive。今回すべて読み取りのみ。

1. hotfix/windows-publication-hardening → mainのPR差分とWindows/Ubuntu CIを確認し、ユーザーがmain取り込みを承認する。developへも同じ修正を反映する計画を維持するが自動マージしない。価格・履歴・過去noteに差分がないことを再確認する。
2. 既存週次ChatGPTタスクの06:40 JSTと世代結合ルールを本人が確認する。新しいタスクは作らない。
3. Repository Settings → Pages → Build and deployment → SourceをGitHub Actionsへ変更する。既存公開サイトを削除しない。
4. Settings → Environments → github-pages → Deployment branches and tagsをSelected branches and tagsのmainのみで維持する。無人実行のためRequired reviewers/Wait timerがないことを確認する（現在は適合）。
5. Settings → Actions → GeneralでActionsを許可する（現在は適合）。Workflow permissionsの既定Read repository contentsは変更不要。jobごとにprepareはcontents/actions read、note保存はcontents writeだけ、deployはcontents/actions read・pages write・id-token writeを指定する。広い既定writeやPR承認権限は不要。
6. 最後にSettings → Secrets and variables → Actions → Variables → New repository variableでWINDOWS_PIPELINE_ENABLED=trueを登録する。承認前には作成しない。GitHub Actionsへの切替と有効化は実公開を可能にするため、コードレビューとは別の明示承認が必要。
7. 当日のWindows取得→日次→週次→公開の成功を実際の結果で確認する。prepareの成功やCIを本番公開成功と扱わない。

note.com実投稿は含まない。既存クラウド投稿バッチはmainの共通manifest schema_version=1、site_id/issue_key/記事SHA-256の台帳で重複を防ぐ設計だが、外部バッチの実環境・認証・停止状態・実台帳との受入れは未検証。note/queue-state.jsonとnote_draft_ledger.pyは別のローカル補助であり、公開済み台帳の実在確認を代替しない。クラウドの既存投稿処理がGit保存だけで投稿を開始する設定なら、遅延復帰と競合し得る。本人が既存クラウド投稿を停止状態に維持したことを確認するまで、投稿を含む完全自動運用は承認しない。サイト公開と外部note投稿の残リスクは分ける。

停止時は未公開の成果物を捨て、旧正常サイト・履歴・投稿台帳を保持する。WINDOWS_PIPELINE_ENABLEDの無効化は公開停止のための別途承認操作。復旧は本来の生成元/時刻のまま再検証し、データや成功履歴を巻き戻さない。旧Oracleシェル3本はexit 3のままで、現行Windows/ActionsはVMへ接続しない。外部クラウド投稿バッチ自体のホストがOracleかどうかはこのリポジトリから確定できない。

### CIで判明した既存fixtureの時刻依存

初回CIはHISTORY_FROM_FUTUREで失敗した。既存取得テストの時計は10月21日固定だが、履歴だけ最新の本番ファイルを一時領域へコピーしていたため、10月23日の正常な実履歴を拒否した。本番側の未来履歴拒否は変更せず、一時領域の取得用履歴を明示的な固定fixtureへ分離した。未来履歴がAPI呼出し前に拒否される回帰テストも追加した。日曜の正常/異常シミュレーションと実データのprepareは別に扱う。hotfixではPR CIだけを使用し、pushとPRで同じ全件試験を重複起動しない。

## 2026-11-02: ChatGPT編集から独立する復旧案（未有効化）

### 確認できた原因と現在地

- 10/28は06:10:01起動、06:10:03終了コード1。06:09:45のスリープ/ネットワーク切断後、接続復旧は06:10:11だった。空のFETCH_HEADと新規収集世代なしからGit取得段階の停止と判断する。標準エラーが保存されていなかったため当時の具体的な通信エラーは未確定。
- 本人がACの通常/無人スリープを0に変更済み。11/2にも両方0、直近夜間スリープ記録0を確認。10/29、10/31、11/1、11/2の収集は終了0。10/30はイベント332で実行ユーザー非ログオンのため未起動だった。RDP切断とログオフは別で、ログオン維持の条件は残る。
- 11/2のmain d9c1ac2805a3d6368ce1ea099e9ac093e802fc3aに当日06:10の商品がある。8カテゴリ、履歴498商品・6,149記録。日次JSONは10/27（generatedAt=2026-10-26T21:53:55.161Z）、週次は10/25（2026/10/19〜2026/10/25）のまま。現在の商品ハッシュと一致しない。
- 既存公開workflowはschedule/手動だけで、定期実行が09時台以降へ遅れ、3時間鮮度の判定前提にも間に合わない日がある。編集停止とschedule遅延は別々の原因。安全判定で古い編集/商品を公開しない動作は維持する。
- 本人から既存2編集タスクの有効状態/実行結果は確認できず、編集監視タスクは無効との回答を受けた。以後はChatGPTタスクが動く前提を外す。登録タスクの追加/変更/自動削除はしない。

### 独立したWindows完結処理

新しい入口はscripts/run_windows_daily.py。承認後に既存06:10タスクのアクションだけを切り替える案であり、新しい定期タスクは作らない。今回、登録済み本番タスク・配置済みPythonモジュールは変更していない。

1. 06:10〜06:20の開始制限を維持。同じユーザーのGCM/楽天資格情報、明示プロキシ、TLS/ホスト名検証を使う。mainのconfig/automation-owner.jsonがwindows指定であることをAPI前に確認する。当日取得済みの正しい世代があれば再利用し楽天APIを再取得しない。未取得の場合だけ既存run_updateを使い、API予算・履歴保全・06:25までの取得/収集push制限をそのまま適用する。
2. 実時計で06:30まで待機し、最新mainを読み直す。価格・レビュー・送料等が存在することと、既存isDeal判定だけに基づく定型の日次編集を作る。数値/商品仕様/最安断定を生成しない。全カテゴリを動的に扱い、特価候補なしなら空配列、topPickCodeは空文字にする。実編集時刻・実商品ハッシュだけを記録し、日次JSONだけを1コミットで通常pushする。
3. 日曜だけ実時計06:40以降に最新mainを再取得し、同じ商品世代の日次以降の週次JSONだけを生成/検証/通常pushする。過去週の記事/画像/ZIPや履歴を更新しない。平日は当週日曜の正しい保存済みnoteを再利用する。
4. 06:50以降、Windowsの一時Git snapshotでサイト/比較ページ/日曜note本文/PNG/ZIPを生成し検証する。ローカル生成物をそのまま公開しない。鮮度・カテゴリ・商品ハッシュ・編集順序・既存note契約をすべて通過した場合だけ、既存windows-daily-publish.ymlをmain指定で1回workflow_dispatchする。
5. Actionsは現行のprepare/persist-note/deployを再実施する。Windowsは最大45回/約15分の読み取り確認だけを行い、実際のPublish Pages成功を確認した場合だけ成功終了する。依頼受理を公開成功として扱わない。失敗・不明・待機期限超過は失敗終了し、自動で再dispatchしない。GitHub側のqueueが長い場合、予定時刻の完了は保証しない。

既存scheduleは補完として残す。公開workflow、Pages設定、公開Variableは今回変更しない。Windowsによるdispatchは既存のconcurrencyと当日成功レシートに合流する。実行中の公開があれば追加依頼せず結果を観測し、成功済みなら再公開しない。ローカルのdispatch intentを送信前に保存し、送信結果不明の再送を拒否する。これは「必ず成功」ではなく、二重公開と不明結果の自動再送を防ぐ設計。

### 競合防止と秘密情報

config/automation-owner.jsonでeditorialOwner=windowsを宣言する。AGENTS.mdと旧2プロンプトは、その場合クラウド側を読み取り/報告だけにする指示へ変更した。ただし既存ChatGPTタスクの登録内容自体は変更していない。切替時に本人が残存タスクを確認し、見つかれば無効化または読み取り専用指示へ変更する。外部の書込み権限自体をPythonから取り上げたとは扱わない。

別の編集処理が既に同じ商品世代の有効なJSONを作っていれば再利用し、上書きしない。同じ週の異なる商品世代や不正編集は停止。Git push直前のmain比較と非force push、完結処理全体の排他と既存収集の排他、Actions共通concurrency/成功レシートで競合・二重公開を防ぐ。その他のサイト/外部note投稿処理には変更しない。

GCMのトークンは子プロセスの標準出力からメモリ内だけで取得する。対象repoのContents read/writeに加え、Actions read/write（workflow_dispatch）が必要。APIはこのrepoの該当workflowのGET/dispatchだけを許可し、redirect・TLS無効化・自動再試行はしない。11/2の同じ通常ユーザーによる読取確認でGCM取得、workflow GET HTTP 200、repoスコープありを確認した。これは[GitHub公式のdispatch認可要件](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)と整合するが、本番dispatchの実通信は未実施。APIバージョンを2026-03-10へ固定し、現行200/実行IDと従来204の両方を検証する。200時は返された数値IDで追跡し、204時は事前Run ID集合との差から1件だけを識別する。未知の応答や複数候補は停止する。

私有runtime/daily/logsにUUID別の固定スキーマJSONを保存する。実時刻、処理段階、成功/失敗、許可された固定エラーコードだけを記録し、秘密値・応答本文・URL・任意例外文字列を保存しない。ログが書けなければ停止する。未ログオンでタスクが起動しない場合はPythonログを作れないため、Task Schedulerのイベントと外部の公開停止通知も確認する。

### 承認後の切替手順・停止条件・切り戻し

1. hotfix/daily-publication-recovery → mainのDraft PRとWindows/Ubuntu CIをレビューする。mainへの取り込みと本番切替の承認は別途。developへのbackportも必要だが自動マージしない。
2. 現在は当週11/1の正しい週次セットがmainにない。平日開始で11/1の日付やハッシュを補完してはならない。初回は11/8（日）06:10からの実取得→06:30日次→06:40週次→06:50検証/公開依頼を推奨する。平日からサイトだけ復旧する別方式は未実装・未承認。
3. task XML、個人用設定、コード、最新mainの独立バックアップをOneDrive外へ保存し、ハッシュ/復元を確認する。承認済みコミットの全必要モジュールを新しい固定バージョンの配置先へ置く。旧バージョンは削除しない。
4. 既存タスクNoranamani-GadgetDeals-Windows-0610を一度無効にし、同じPython/通常ユーザー/InteractiveToken/06:10/IgnoreNew/OneDrive外の作業場所を維持して、run_windows_daily.py --config 個人用設定 --applyへ変更する。06:30〜06:50の実時計待機があるため、15分の実行上限を75分へ変更する。PASSWORD/S4Uや新規タスクは不要。登録・有効化は今回行わない。
5. GCMの対象repo限定権限、30日Affiliate確認期限、TLS/CA/プロキシ、ログオン維持、残存編集タスクの読み取り専用化を確認する。--applyなしは計画表示だけ（ネットワーク/資格情報読取り/ファイル変更なし）。最初の本番試験は別途承認する。
6. 本番データ、価格履歴、過去記事、Pages公開元、公開Variable、note.com投稿設定はこのPR作業では変更しない。コードの実装/CI成功と本番切替/自動公開成功は別の状態として記録する。

異常時は時刻・ハッシュ・古い記事を加工せず停止する。公開結果が不明ならintentやActions履歴を削除して再試行しない。サイトは最後の正常デプロイを維持する。切り戻しは新入口を止め、バックアップした既存タスク定義と旧固定コードへ戻す（収集だけの動作に戻る）。main/products/history/note台帳をresetやforce pushで巻き戻さない。編集停止状態が解消したとは扱わず、クラウド編集を勝手に再有効化しない。

この処理はnote素材を作るが、note.comへログイン・下書き作成・投稿しない。既存クラウドnote投稿が有効なら保存素材を独立して利用し得るため、本人が別運用として状態を確認する。note認証問題の解決や実投稿成功は、このサイト復旧PRの成功に含めない。

### 検証

新規試験は外部API/実資格情報なしの固定fixtureとローカルbare Gitで実施。日曜の全段階、翌平日のnote保持、当週note不足での停止、既存編集再利用、未知カテゴリ、値下がり候補なし、時刻制限、二重起動、Git競合、API失敗、公開要求重複、結果不明、Pages失敗、固定ログの秘密値拒否を含む。本番価格・履歴をfixtureへ採用して時刻を補完したとは扱わない。

ローカルでは既存回帰を含む205件、およびAPI応答仕様追従後の新規33件が成功した。PRの最終Windows/Ubuntu CIは別途結果を記録する。実API取得、本番mainへのデータ更新、実dispatch/Pages公開、タスク切替、クラウドタスク変更は行っていない。

従来fixtureが変動する本番のtopPickCode/同週manifest/archiveをコピーしていたため、最新商品との不一致で本来の検査対象より前に停止するケースがあった。固定日曜/初回移行の一時fixtureを分離し、実運用側の厳格な検証は弱めていない。CIは既存のWindows/Ubuntuオフラインworkflowを使い、公開workflowは起動しない。

## 2026-11-02: PR #4レビュー対応（本番未切替）

### レビューの範囲と公開結果の修正

[会話コメントの全5項目](https://github.com/noranamani/noranamani-gadget-deals/pull/4#issuecomment-6566277240)を確認した。追加のインライン指摘・レビュー提出は確認時点で0件。直前のWindows/Ubuntu CIは各206件成功（[run 41367026985](https://github.com/noranamani/noranamani-gadget-deals/actions/runs/41367026985)）。この既存206件を維持して回帰試験を追加する。前節の「成功終了」は、以下の公開結果の区別を含む意味に改める。

従来はawait_publicationが既存公開を確認してNoneを返しても、orchestrateが無条件でpublishedを返した。publication_gateの成功判定から実際のrun IDを返すpublication_receiptを分離し、観測結果を必須の構造に変更した。既存のCLI向けpublication_neededはboolの契約を維持する。

| publicationStatus | 確認条件 | Windows終了 |
| --- | --- | --- |
| published | dispatchのHTTP 200が返した数値run IDのPublish Pages成功 | 0 |
| already_published | 今回の要求前から当日mainの公開成功が存在し、再確認できた | 0（新規公開とは報告しない） |
| observed_other_run | 追加要求をせず監視した実行、または自分の受理IDと異なる実行の公開成功 | 0（他の実行による成功） |
| observed_unattributed_run | 旧HTTP 204で自分の要求との帰属を断定できないが、特定run IDの公開成功は確認 | 0（自分の新規公開とは報告しない） |
| unknown | 応答喪失、観測不可、タイムアウト、Pages試行の失敗/取消など到達結果が不明 | 1・自動再要求しない |
| failed | 明示的な要求拒否、または観測したworkflowが終了し公開未試行/skipで他の成功もない | 1 |

成功扱いには正の整数run IDとverified=trueが必要。None、run ID欠落、workflow全体のsuccessだけ、Publish Pagesのskipは成功の証拠にしない。API/権限が監視途中で失われても失敗を断定せずunknownとする。HTTP 204の候補が複数なら不明で停止する。[dispatch公式仕様](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

当日公開済みの記録は重複公開を止める証拠であり、今回作った商品世代をその実行が公開した証拠ではない。結果のsourceCommit/productsSha256は今回ローカルで検証した入力で、公開済みサイトの世代と同一とは断定しない。表示する公開実行IDのActions履歴で区別する。

intentの受理workflowRunIdは他実行のIDで上書きせず、confirmedWorkflowRunId/publicationStatusを別に保存する。既存intentがあっても成功の読取確認は可能だが、未確認なら再dispatchしない。ログにもpublicationStatus/publicationRunId/publicationVerifiedを保存し、成功証拠なしのfinish(success)を拒否する。公開成功後のログ保存失敗は終了1としつつ、確認済みの公開事実を消して未公開とは報告しない。秘密値・任意例外・返却URLを記録しない。

### 起動・通信の安定化と実設定の読取確認

- 初期main取得だけ、10秒待ちを挟み最大3回の読み取り再試行を追加。起床直後のネットワーク復帰待ちを目的とする。各試行の前に06:10〜06:20を再確認し、期限外なら停止。Gitの不正SHA/形式エラーは再試行しない。商品取得、commit/push、dispatchは再試行しない。以降の通信障害も不明な書込みを再送せず停止する。
- network_readiness段階を固定ログへ追加。二重起動ロック、日別dispatch intent、API予算、06:25収集期限、06:23:30以降の収集push禁止、Git競合停止、当日公開レシート、Actions concurrencyは維持する。失敗したログ・入力snapshot・intentを削除して成功したように再実行しない。
- 同じ通常ユーザーのSID一致、楽天資格情報3件、Affiliate確認記録、非対話GCMの資格情報読取を確認。確認期限は11/20 14:37 JSTのまま、30日を延長していない。明示プロキシを使用する設定とTLS証明書/ホスト名検証有効、固定本番コード一致を確認。楽天APIと公開POSTは未通信。
- 既存タスクは212件。対象は有効・Interactive・IgnoreNew・実行上限15分、StartWhenAvailable/WakeToRun/ネットワーク必須はいずれもfalse。11/2 06:10の最終結果0、次回11/3 06:10 JST。入口は旧run_windows_update.pyの収集だけで、新しい完結処理へは未変更。新入口には最大75分への変更が必要だが今回登録・設定変更はしない。AppDataではなくユーザープロファイル配下のOneDrive外の固定配置を維持する。

### Windows Update後の無人復旧は未成立

Interactiveはユーザーが既にログオンしている場合だけ動く。Windows Update等による再起動後、未ログオンなら起動できない（10/30に実例あり）。ネットワーク待ちだけでは解決しない。RDP切断はログオフと異なるが、切断セッションをポリシーでログオフする環境では同じ制約がある。

非ログオン運用の候補は同じ通常ユーザーのPASSWORD方式であり、S4Uはネットワーク・暗号化ファイルに制限があるため採用しない。PASSWORDはタスク登録時に本人がWindows正規画面へパスワードを入力する必要があり、batch logon権限/組織ポリシー、Credential Manager/DPAPI、GCMの非対話認証、プロキシ・CAの実挙動を別環境で確認しなければならない。本人のブラウザログインや今回の対話ユーザーの読取成功を、非ログオン成功の証拠にしない。自動ログオン、パスワード平文保存、セキュリティ設定緩和は行わない。

承認があれば、本番タスクを触らず、同ユーザーの一時PASSWORDタスクから資格情報/確認記録/GCM/HTTPSの読取りだけを試験し、本人承認のログオフ・再起動後にも確認、終了後に削除する。今回この試験は未実施。PASSWORD/S4Uは不要とした前節の方針は「ログオン維持運用の場合」に限る。再起動後も本人操作なしという最終要件に対しては未完了と判定する。

[Microsoftのログオン種別仕様](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_logon_type)、[タスクのセキュリティコンテキスト](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks)を参照。Windows Updateの無効化や再起動抑止ポリシーの変更は今回実施していない。ログオン維持運用を選ぶ場合も、更新後の再ログオン、AC給電/スリープ無効、証明書・プロキシ接続、06:10前の起動確認が運用条件として残る。

### 11/8開始とnoteの分離・公開遅延時の復旧

11/1分は生成日時/ハッシュを補完しない。11/8（日）の固定fixtureで06:10収集世代→06:30日次→06:40週次（11/2〜11/8）→06:50本文/PNG/ZIP・比較ページの生成と検証を追加試験する。fixtureの日付・確認期限は試験専用の値であり、本番データへ採用しない。過去の履歴/11/1記事を変更しない。翌平日の既存note再利用・欠落/遅延/不正ハッシュでの停止は既存試験も維持する。

15分の監視期限を超えた場合はunknownで終了しintentを保全する。Actionsのrun ID/Publish Pages結果を読み取り確認し、実際の成功が分かれば再公開せず解決する。Pages到達が不明な試行を再実行してはならない。公開未試行で編集だけが遅れた場合は、既存07:15/07:45/08:15チェックが厳密な鮮度と世代条件を満たす間だけ回復する。3時間鮮度や成果物30分期限は緩和しない。全枠欠落や長時間のGitHub障害は自動成功を保証しない。

このPRにはnote.comのログイン・投稿・投稿台帳更新を含めない。Windows/Actionsはnote用素材の生成・保存までを担当する。既存noranamani-note-cloudのセッション、投稿スケジュール、4つのゲート、重複防止と結果確認は別運用の受入れ条件であり、このテスト成功を実投稿の成功と扱わない。

### 本番切替の残作業・停止条件

1. 同じPR #4（hotfix/daily-publication-recovery → main）でWindows/Ubuntu CI・変更内容をレビューする。Draftを維持し今回はマージしない。main取り込み後のdevelopへのbackportも別途判断する。
2. 既存ChatGPT編集が書き込まない状態とOracle停止を再確認。main、task XML、固定コード、個人用設定を独立バックアップし、復元確認する。承認済みコードを新しいOneDrive外の固定配置へ置く。
3. ログオン維持運用か、別途試験済みPASSWORD運用かを本人が選択する。後者の未検証状態では完全無人運用を開始しない。承認後だけ既存タスクの入口/作業場所/75分上限を変更。StartWhenAvailableで時刻外に動いても収集開始条件を緩めない。
4. freshな実取得・編集世代による最大1回の本番dispatch受入れ試験は別途承認する。HTTP受理、実行ID、実際のPublish Pages成功、公開済みnote素材、ローカルログを確認。現在の古い編集を使って試験しない。note.com実投稿は含めない。
5. 11/8の実時刻・実データで初回監視し、以後の通常運転を判断する。コード/CI完了、本番設定完了、本番自動実行成功を別々に記録する。未知の公開結果、Git競合、履歴/世代不一致、認証/期限切れがあれば停止。時刻やハッシュを補完しない。

切り戻しは新入口を停止し、保存した旧タスク/コードへ戻す承認操作とする（旧方式は収集のみ）。公開済みサイトは最後の正常状態を維持し、価格履歴・公開履歴・note投稿台帳を初期化せず、mainへreset/force pushしない。今回、本番タスク・コード配置・設定・データ、Pages設定/Variable、note-cloudは変更しない。

### 検証記録

既存206件に、公開の所有者と証拠、None拒否、既存公開/他実行/204、不明/失敗の固定ログ、監視タイムアウト、公開後のログ/receipt保存失敗、初期Git読取復帰・期限超過、11/8の正規順序を検証する30件を追加。計236件を対象にする。ローカルWindows実行と、同じPRのWindows/Ubuntu CIの最終結果・コミットSHAはPR本文へ記録する。本番dispatch/Pages公開、PASSWORD試験、note.com投稿は未実施。

## 2026-11-02: 週次素材から日次公開を分離する緊急復旧（承認前）

### 確定した現在地と停止原因

- 最新mainはd9c1ac2805a3d6368ce1ea099e9ac093e802fc3aのまま。商品は11/2 06:10:02 JST、8カテゴリとqueriesの順序/ID、商品・履歴・queriesのGit原本SHA-256は一致。履歴498商品・6,149記録。日次編集は10/27 06:53:55 JSTで商品ハッシュ不一致、週次は10/25 07:41:29 JST（10/19〜10/25）のまま。
- [最新公開run 41377949339](https://github.com/noranamani/noranamani-gadget-deals/actions/runs/41377949339)は11/2 11:28 JST開始。prepareのログはPRODUCTS_STALE_OR_FUTURE、deployはskip。収集失敗ではなく、編集未更新とschedule遅延が継続している。公開サイトはHTTP 200だが表示は10/26 06:10更新で、サイト更新が復旧したとは扱わない。
- PR #4の6b762c9は未マージ・本番未配置。前回の236件Windows/Ubuntu成功を確認。現在の本番Windowsタスクは旧run_windows_update.py・Interactive・15分上限で収集のみ。11/2の結果0、次回11/3 06:10。現在210タスクを読み取り基準として記録し、他タスクは触らない。資格情報3件/確認期限/GCM読取り/TLS/固定本番コードは再確認済み。
- 従来のvalidate_publicationは平日も前日曜の週次JSON/manifest/記事/画像/ZIPを必須とし、初回平日公開が11/1の不足で停止する。これは日次サイトに不要な依存。安全条件を一括無効化せず、用途別の検証へ分離する。

### 変更する契約と維持する保護

1. purpose=siteはvalidate_dailyを通し、商品/履歴/queries/site設定のハッシュ、帰属確認期限、当日06:10以降・3時間以内、06:30以降かつ収集後の実日次編集、商品ハッシュ、全カテゴリ、掲載商品コードを検証。HTML/比較ページを生成してvalidate.pyを通し、docsだけを成果物にする。週次JSON/noteフォルダを入力条件にしない。
2. purpose=noteは日曜限定。これまでの厳格な商品/日次/週次の順序・時刻・同一商品世代、本文/PNG/ZIP/manifest/archiveの整合性を維持。noteだけを成果物にする。同じ週の保存済み記事は原文を再利用し、変更・破損なら停止。平日に古い週の価格や日時を更新しない。従来combined契約も互換用に残す。
3. 成果物にpurposeを結合し、siteにnoteファイル、noteにdocsが混じる場合や、用途を取り違えたverify-currentは拒否する。30分の成果物期限、mainのCAS確認、通常push、当日公開レシート、Actions共通concurrency、最大1回のdispatchは維持する。
4. Windowsは06:30日次、日曜06:40週次、06:50サイト検証を維持。週次編集/素材の既知の検証エラーはnoteだけblockedとして固定コードを保存し、日次サイトを継続する。欠けた週次を初期化しない。秘密情報、Git競合/不明な書込み、予期しないエラーは無視せず全体停止。出力のweeklyNotePreviewStatusはローカルの素材検証結果であり、note保存/実投稿の成功ではない。
5. Actionsのprepare/deployはsite専用。weekly-noteは日曜にdeploy終了後、最新mainからnote専用prepare/persistを実施する。deployがnoteの成功を待たない構成とし、noteコミットが進行中のサイト検証のmainを変える競合を防ぐ。noteジョブが失敗してもPages成功の事実は維持し、reportはnote失敗を別に記録する。サイト成功済みの補完実行でも日曜素材を再検証できるが、同週の上書きや二重Pages公開はしない。
6. Windowsの監視は独立したnoteジョブの終了待ちにせず、in_progressのworkflowでもPublish Pagesステップの実成功を確認できる。週次ジョブの遅延だけで、確認済みのサイト公開を結果不明に戻さない。

既存236件を維持し、用途分離・平日/日曜のnote欠落/不正・厳密な鮮度/カテゴリ・用途取り違え・同週再生成拒否・Pages成功後のnote実行中など22件を追加。ローカルWindowsは計258件すべて成功。テストは一時fixtureのみ。Windows/Ubuntu CIの最終件数・結果・コミットSHAはPR #4本文へ記録する。workflow YAMLもローカルで解析し、deployはprepareだけに依存し、weekly-noteはprepare/deployの終了後になることを確認した。

GitHubの実設定も読取確認済み。Pages build_type=workflow、WINDOWS_PIPELINE_ENABLED=true、github-pages environmentの許可ブランチはmainのみ。これらの設定変更は実施しておらず、現在の設定を維持して切替準備できる。

### 本日の実データによる事前判定

11/2 13:47 JSTの実時計で、最新mainをGit archiveした一時入力に新しいsite用prepareを適用。取得から約457分経過しているためPRODUCTS_STALE_OR_FUTUREで生成前に停止し、data/noteの全ファイルハッシュは不変。日付・SHA・価格・編集日時を補完して通していない。実データの正常系公開準備は鮮度切れで未実施、正常系は固定fixtureの生成/検証と区別する。

### 最短再開案・承認対象・停止と切り戻し

前節の「11/8開始を推奨」「非ログオン未検証で切替保留」は、日次サイトの早期復旧についてはこの節で更新する。Windows再起動後の完全無人化は後続課題とし、現在のログオン維持運用で日次再開できる。11/8まで待つ必要はない。

- 最短の通常運用案は11/3（火）06:10の正規取得→06:30日次→06:50以降のサイト公開。前提は前日までの本人承認、PR取り込み、バックアップ/固定コード配置、既存タスク入口と75分上限への切替、ログオン/給電/ネットワーク、GitHub権限/有効設定の確認。予定時刻の成功はGitHubの待ち時間等に依存し、保証しない。Windows Update後のPASSWORD試験はこの再開の前提にしない。
- 本日の古いデータを公開しない。時刻外の新規API取得や開始窓回避は今回実施・承認していない。本日中の別途復旧が必要なら、現在時刻の正規再取得を含む一度限りの手順・API回数・同日UTC価格競合を別に評価し、本人承認が必要。今回その手順を勝手に実行しない。
- 本番操作は承認を分ける。PR #4のmain取り込み、固定コード配置/既存Windowsタスク変更、一度の実公開試験は、それぞれ承認前に実施しない。Pages設定・公開Variable・note-cloud・ChatGPTタスクを変更する必要は今回確認していない。残存ChatGPT編集が書き込まない状態は切替時に確認する。
- 取得/日次の時刻・ハッシュ・カテゴリ・履歴・帰属/認証に問題、Git競合、不明な書込みがあれば停止。noteだけの検証失敗なら素材の保存/投稿を止め、正常な日次サイトの公開は妨げない。公開結果不明はintentを保持し、実際のPages結果を読取確認するまで再dispatchしない。
- 公開失敗時は最後の正常サイトを維持し、新入口を停止する。承認のうえ保存した旧タスク/固定コードに戻せるが、旧方式は収集のみで編集復旧にはならない。価格履歴・投稿台帳・公開記録を初期化せず、mainをreset/force pushしない。既に新サイトの公開が成功した後の内容問題は、保存済み正常成果物の再検証と別途承認で対処する。

今回はPR用コード/試験/手順書の更新まで。本番API取得、main/developへのpush/マージ、タスク変更、公開workflowの起動、Pages実公開、note.com投稿は行わない。実際の公開成功を確認するまでは「復旧準備完了」であり、「復旧完了」ではない。

## 2026-11-02: 障害分離・永続状態・再開経路の追加

用途分離のc48b84af8bae466db5527c7c07f5dae65bb1b656はWindows/Ubuntu各258件成功（[CI 41390737756](https://github.com/noranamani/noranamani-gadget-deals/actions/runs/41390737756)）。追加要件として、recovery_state.pyの原子的な段階別状態、recovery_lock.pyの中断回復可能なOS排他、--resume/--status、bounded GET/Git読取バックオフ、画像なし表示、無効状態の復旧タスク定義例を準備した。

詳しい依存関係、障害別継続/停止表、状態スキーマ、上限と再開条件、通知・単一障害点、本人承認が必要な本番操作は[windows-recovery-design.md](windows-recovery-design.md)へ記録した。API中断・push不明・公開不明を再送せず、mainの確定結果で照合する。週次/note投稿の失敗をサイト失敗へ結び付けないが、日次自身の鮮度・整合性と秘密情報検出は維持する。

新しい復旧トリガーはレビュー用XMLに過ぎず、登録/有効化していない。再起動後の未ログオン実行、開始窓を過ぎた未収集の自動取得、物理故障からの復旧を保証しない。これらを隠して「完全自動復旧済み」と報告しない。最終CI結果と最新コミット、承認対象はPR #4本文を正として確認する。

ローカルWindowsでは最終295件（前段258件＋障害注入37件）が成功。価格履歴/過去noteを変更しない一時fixture、実際に終了させた試験用子プロセス、疑似GitHub応答で検証した。商品画像の失敗ハンドラーも合成DOMで「画像なし」へ切り替わることを確認。未実施の本番試験を成功と報告しない。
