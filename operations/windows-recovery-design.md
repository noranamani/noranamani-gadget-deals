# Windows更新の障害分離・復旧仕様

2026-11-02作成。PR #4のレビュー対象。コード・一時fixtureの検証と、本番適用・実公開成功は別の状態として扱う。本番切替前には本人の承認が必要。

## 現在地と優先順位

1. 日次サイトの再開を優先し、週次noteや非ログオン運用の完成を待たない。
2. 正常な日次運用と、失敗した段階だけを再開する経路を用意する。
3. Windows Update後の未ログオン実行は別の受入れ試験とする。

mainの商品取得は11/2 06:10 JSTまで成功。履歴498商品/6,149記録とGit原本のハッシュを確認済み。日次は10/27、週次は10/25で停止し、公開サイト表示も10/26のまま。直近の公開run 41377949339は11/2 11:28開始、PRODUCTS_STALE_OR_FUTUREで停止。PRの未適用、編集停止、schedule遅延、週次素材への不要な依存が重なっていた。13:47の新コードによる実データ事前判定でも取得から457分経過しており、生成前に同じ鮮度エラーで止まった。時刻・ハッシュは補完していない。

最新main/PR/失敗run/実時計の結果はoperations/windows-migration.mdとPR #4本文に記録する。本書のfixtureの日時やハッシュを、本番ファイルへコピーしてはならない。

## 処理単位と依存関係

| 単位 | 担当 | 必須入力 | 成果・成功の証拠 |
| --- | --- | --- | --- |
| collection | Windows | 資格情報3件、帰属確認、queries、既存履歴、許可時刻 | products/history/statusの同一コミット・厳密なハッシュと履歴保全 |
| daily_editorial | Windows | 検証済み当日収集 | 実時刻・同一商品SHA・全カテゴリの日次JSON。1ファイルだけ通常push |
| site_build | Windows/Actions | 収集と日次編集 | purpose=site、HTML/比較ページ/告知・数値の検証、docsだけの成果物 |
| publication | Actions | 有効なsite成果物、最新main、重複公開ゲート | 実際のPublish Pagesステップ成功とrun ID |
| weekly_editorial | Windows・日曜 | 同一世代の収集/日次、06:40以降 | 実週・実時刻の週次JSON。過去週や同週の異なる世代を置換しない |
| note_materials | Windowsプレビュー/Actions | 正しい日曜週次・収集・日次 | purpose=note、本文/PNG/ZIP/manifest/archiveの整合性 |
| note_persist | Actions | 検証済みnote成果物 | note専用コミット。Windows側はGitHub jobと保存済みファイルを読取照合した場合のみ成功確認 |
| note_posting | 別repoのnote-cloud | note側の移行ゲート・認証・投稿台帳 | このPRでは実行・成功記録をしない。既存外部台帳が唯一の投稿結果の根拠 |

日次サイトの入力に週次JSONやnoteディレクトリは含めない。purpose=siteはvalidate_daily、purpose=noteは日曜限定の従来の厳格な週次検証を使う。旧combinedモードは互換用に残すが、現在のworkflowとWindows入口は用途を明示する。

Actionsのdeployはprepareだけに依存する。weekly-noteはdeploy終了後に実行し、サイト公開の成否とは別に結果を記録する。この順序はnoteコミットが進行中のサイトmain確認を競合させないためであり、deployがnote成功を待つ依存はない。noteジョブが失敗してworkflow全体がfailureでも、Publish Pagesの実成功があればサイト公開の成功は保持する。

## 障害時の動作・復旧表

| 障害 | 継続する処理 | 停止・再開方針 |
| --- | --- | --- |
| note.comの投稿失敗/認証切れ | 日次取得・編集・サイト公開 | note-cloud側の認証/台帳で対処。このrepoから再投稿しない |
| 週次JSONが7日以上古い/欠落/破損 | 正常な当日サイト | 素材だけblocked。過去日付・価格を補完しない |
| 週次JSONのpush結果不明 | 日次を最新mainから再検証して継続可能 | 週次状態unknown、同じpushは再送しない。実mainの正しい週次が見つかれば照合で解決 |
| noteのPNG/ZIP生成失敗 | サイト | 素材生成だけblocked。カバー必須のnote契約を弱めず、古い日付の画像を流用しない |
| サイト商品画像の読込失敗 | サイトの本文・比較・価格 | 固定onerrorで画像を隠し「画像なし」へ。価格や文章は変更しない |
| 商品取得失敗 | 検証済みの当日世代がmainにあれば後続処理 | 当日・3時間以内・完全な同一世代に限り再利用。取得途中のAPIを自動再送しない |
| 起床直後のGit読取不通 | なし・安全に待機 | 同一読取を最大3回、10秒/20秒バックオフ。時刻外/不正形式は停止 |
| GitHub REST GETの一時不通/5xx/タイムアウト | 成功済みの段階は保持 | GETだけ最大3回、2秒/4秒バックオフ。POSTの再試行はしない |
| HTTP 401/403認証、CA不正 | 他の独立した正常処理だけ | 当該試行は即停止。TLS/認証チェックを緩めず、本人が認証/CA/権限を修復する |
| 429/明示的rate limit | 成功済みの段階を保持 | 即時再試行しない。制限/次の許可枠を確認。楽天APIのQPS/予算も従来どおり |
| 日次pushの応答喪失/途中終了 | 書込みは保留 | main上の実JSONと入力/出力ハッシュが一致すれば成功確認。確認できなければ再pushしない |
| site成果物の破損/30分失効 | 正しい収集・編集は再利用 | 現在も鮮度内ならサイト成果物だけ再生成。古い成果物は公開せず、ファイルも消さない |
| HTML/数値/公開内容の検証失敗 | 直前の正常サイト | 新サイトを公開しない。古い商品/編集を最新として再公開しない |
| 公開要求の応答喪失 | 読取による照合 | intentを保持。runを照合・監視するだけで再dispatchしない |
| Publish Pages試行の失敗/取消/到達結果不明 | 直前の正常サイト、独立したnote処理 | unknownで保留。公開未試行と区別し、確認前に再公開しない |
| 公開前の生成失敗 | 直前の正常サイト | 既存Actions補完チェックが鮮度内で再生成可能。Windowsからの再dispatchは行わない |
| 途中終了・再起動 | 成功済み入出力を保持 | OSロック再取得後、main/状態/成果物を照合して未完了段階だけ再開 |
| 二重起動 | 先行実行 | 非ブロッキングの排他で後続を停止。復旧タイマーは明示的skipped、公開成功とは記録しない |
| 永続状態JSONの破損/書込不能 | 直前の正常サイト | 初期化せず停止。状態を消してAPI/公開をやり直さない |

全体の共有基盤（ディスクやGitHub全体）が壊れた場合まで、すべての処理が継続するとは保証しない。秘密情報検出、不明な例外、日次自身の不整合は安全側へ停止する。

## 永続状態の仕様

OneDrive外の私有runtime/daily/state/YYYY-MM-DD.jsonに保存する。recovery_state.pyが所有する固定スキーマで、ソースコード/公開GitHubへ状態ファイル自体をpushしない。

- 最上位: schemaVersion、JSTの対象date、実商品のgeneration（SHA-256）、段階別stages。
- 各段階: owner、status、attempts、retryCount、startedAt、updatedAt、lastSuccessAt、lastFailureCode、nextRetryAt。
- 来歴: inputSha256、outputSha256、commit、artifact（私有root配下の制約付き相対パス）、writeIntent。
- 公開: publicationRunId、publicationStatus。実行IDは正の整数だけ。
- confirmationはexecutedまたはremote_validated。別の編集処理が正しいJSONを提供した場合も段階の入力条件は満たせるが、自分の不明なpushが成功したとは扱わない。commitも確認した実mainの値を記録する。
- lastSuccessAtは成功を確認した実時刻。既に同じ入出力の成功を再検証しただけなら維持する。失敗理由は固定コードだけを残し、回復しても最終失敗情報を消さない。
- 書込み前に期待出力のハッシュ・commit・writeIntentを保存。writeIntent=trueかつrunning/unknownは、単純再送の許可ではない。実mainで正しい出力が確認できた場合だけ解決する。
- outputSha256はrunning/unknownでは期待出力、succeededでは確認済み出力。公開のWindows manifestは検証したローカル入力を示し、他の実行/既存公開の実サイトと同一世代とは断定しない。公開済み/他実行の場合はpublicationの入力ハッシュを空にして誤った結合を避ける。
- note_postingはowner=note-cloud/status=externalで固定し、未取得の投稿結果を成功扱いしない。note_persistもActionsの成功と保存ファイルを検証できるまでpending/unknownのまま。

保存は一意な一時ファイルへ書き、fsync・JSON再読取確認を経てos.replaceで原子的に置換する。置換失敗時に直前の完結したファイルを維持する。破損を空の状態へ戻さない。別の商品世代への暗黙の乗換えも拒否し、履歴を初期化しない。

UUID別の既存runログには段階・固定エラー・公開判定・noteプレビュー結果を記録する。スキップはstatus=skippedで、publicationVerified=trueや新規公開成功にはしない。APIキー、Cookie、URL、任意の例外本文、パスワードは状態やログへ保存しない。

## 回数制限・再実行の境界

1. 各段階は原則、通常実行を含め日/世代で最大3試行。再起動してもattemptsを引き継ぐ。正常な入力/出力と有効な生成物は再生成しない。失効/破損した私有成果物だけを再生成する。
2. collectionのAPI取得フェーズは最大1試行。これはAPIリクエスト1回の意味ではなく、従来の全カテゴリ予算（queries件数の2倍まで）を持つ一度の収集トランザクション。失敗・中断後、mainに正しい当日世代がなければ自動再取得しない。
3. 失敗後の次回許可は10×2^attempts秒（最大300秒）。読取内部の短いバックオフと、復旧トリガーの間隔は別に制限する。認証/CA/rate limitを自動的に修復する処理はない。
4. Gitのpushやworkflow_dispatchは通信失敗を理由に再送しない。復旧モードでは既存intentを読み、既存runを照合・観測する。新しい公開要求の権限は与えない。
5. 上限到達、状態不明、API中断は手動調査が必要。状態/intent/履歴を消して上限を回避しない。修復後の追加試行は原因と安全性を確認し、本人承認を受ける。

## コマンドとトリガー案（未登録）

実際の非公開パスは手元の設定を使い、次の例のプレースホルダーは置換する。認証値を引数へ渡さない。

```powershell
# 状態だけ。資格情報・ネットワーク・本番データへのアクセスなし。
python -B -X utf8 scripts/run_windows_daily.py --config PRIVATE_CONFIG --status

# 既定は計画表示。設定や資格情報を読み取らない。
python -B -X utf8 scripts/run_windows_daily.py --config PRIVATE_CONFIG --resume

# 承認済みの復旧実行。enabled=trueも必要。
python -B -X utf8 scripts/run_windows_daily.py --config PRIVATE_CONFIG --resume --apply
```

通常開始は06:10〜06:20。復旧入口は06:10〜09:10に限り、各段階で実データの3時間鮮度を別途確認する。新しいAPI取得には復旧モードでも従来の06:10〜06:20/06:25終了制限を適用し、遅い起動で時刻を偽装しない。06:20以降に当日収集そのものを逃していた場合はMISSED_COLLECTION_WINDOWで停止し、次の正規取得または別途承認した手順が必要。

--statusと復旧開始時の診断は、当日の未処理段階と過去日の未解決日付を読み取る。過去日分を再取得・バックフィルしない。全段階が成功/対象外なら復旧タイマーは何も再実行せずskippedで終了する。実公開結果を改めて確認したという意味ではなく、保存済み成功記録によるスキップである。

windows-recovery-task.example.xmlはレビュー用でEnabled=false。06:20から2時間・10分間隔、ログオン2分後のトリガー、同じ通常ユーザーのInteractiveToken、IgnoreNew、75分上限、Python直接実行を提案する。StartWhenAvailableで遅く起動しても、Python側の許可時刻は緩めない。未置換のXMLをそのまま登録してはならず、この作業では登録/有効化しない。

本番タスクの切替、復旧用トリガーの追加、実API/公開試験は別途承認が必要。既存Actionsの06:50/07:15/07:45/08:15チェックは独立したクラウド側の欠落検知・鮮度内の公開補完として残す。失敗はActionsの結果/summaryに出るが、メール等の通知を必ず受け取れるとは未確認。本人のGitHub通知設定確認が必要。

## 排他と中断への対応

recovery_lock.pyはOSの非ブロッキングロック（Windowsのmsvcrt、Ubuntuのflock）を使う。プロセス終了・再起動でカーネルのロックは解放される。共有guardファイルをunlinkして別inodeに入れ替えない。

旧pipeline.lockが残る場合は、OSロックを取得したうえでPIDの終了を確認できたものだけabandoned-locksへ保管する。WindowsではOpenProcess/WaitForSingleObjectで読取確認し、プロセスの終了操作は行わない。アクセス拒否、PID再利用、形式不明のマーカーは安全に解除できる証拠がないため停止する。通常終了時はPIDマーカーを削除し、旧方式との競合防止も維持する。旧/新コードが混在した同時運用は切替時に止める。

## 検証の範囲

- 前段の用途分離はWindows/Ubuntu各258件成功（run 41390737756、commit c48b84af8bae466db5527c7c07f5dae65bb1b656）。
- 今回は永続状態、原子的置換失敗、途中終了、子プロセスの強制終了後のロック回復、二重起動、ネットワーク/GitHub障害、応答喪失、認証/CA/rate limit、7日超のnote、期限外起動、成功済みスキップ、成果物失効を追加検証する。
- 新規障害注入は37件。前段258件と合わせ、最終コードのローカルWindowsで計295件すべて成功、失敗0。CIの最終結果はPR本文へ記録する。
- 商品画像の失敗ハンドラーは、生成したHTMLから取得して合成DOMで実行し、画像非表示と「画像なし」への切替を確認した。実ブラウザの外部画像サーバー障害やnote.comの投稿は試験していない。
- 本番APIへの追加通信、本番main更新、実公開、Windows再起動/ログオフ、タスク登録は未実施。障害注入は一時fixture/ローカルGit/作成した試験用子プロセスだけ。
- 最終コミット、Windows/Ubuntu全件CI、テスト件数はPR #4本文へ記録する。テスト成功だけで本番復旧完了とは判断しない。

## 残る単一障害点・運用制約

- Windows PC/ディスク/電源/ネットワークと通常ユーザーのログオンが収集側の単一障害点。未ログオンの再起動ではInteractiveタスクは実行できず、本書の復旧コードも起動しない。PASSWORD方式の実試験は別途承認・本人の正規画面での入力が必要。自動ログオンやS4Uへの安易な変更はしない。
- ディスク/状態/バックアップを同時に失う物理故障や電源断に対して、ローカルJSONだけで復旧を保証しない。独立バックアップ、GitHub上の確定データ/公開記録との照合が必要。
- 企業プロキシ・CA・出口IP登録、GCM/楽天認証、GitHubサービス停止、Pages/Actionsの遅延・上限は共有依存。無料枠で時刻どおりの完了を保証しない。
- Affiliate本人確認は30日間を維持する。現在の期限11/20 14:37 JSTまでに正規の再確認が必要で、自動延長しない。
- 収集開始窓を逃し当日世代がない場合、鮮度切れ、上限到達、公開結果不明、秘密情報検出、Git/世代不整合は自動突破しない。
- note-cloudの認証・投稿UI・投稿台帳・重複防止は外部運用。Windowsの状態は投稿成功を示さない。note素材がreadyでも実投稿の成功とは別。

## 本番切替と切り戻し

最短の通常再開案は11/3 06:10正規収集→06:30日次→06:50以降公開。11/8や完全非ログオン化の完成は日次再開の前提にしない。本人承認後、独立バックアップ→PR取り込み→固定コード一式配置→既存タスク入口/75分上限切替→必要なら復旧用定義を設定→初回実公開確認の順に進める。本番操作は今回実施しない。

失敗時は最後の正常サイトを維持し、新入口/復旧トリガーを停止する。承認のうえ保存済み旧タスク/コードへ戻せるが、旧版は収集のみ。状態・dispatch intent・商品履歴・note台帳を消して再実行しない。mainをreset/force pushしない。実際のPublish Pages成功と公開サイトの新しい内容を確認して初めて復旧完了と報告する。


## 2026-11-04 本番GET事前確認による停止と復旧設計の更新

11/4のWindows正規実行は06:10収集・GitHub push、06:30日次編集・push、06:50サイト成果物生成・検証が正常終了した。06:50:09の**公開前のGitHub Actions GET**で `PUBLICATION_STATE_UNAVAILABLE`、通常タスク終了コード1。06:55/07:05の復旧は `STAGE_RESULT_UNCONFIRMED`、07:15〜08:15は `STAGE_RETRY_LIMIT`。当日 `daily/dispatch/2026-11-04.json` は存在せず、公開dispatchの送信・受付は確認されなかった。遅れて実施したGETがHTTP 200だったことは、当時の失敗のHTTPステータスが200であった証拠ではない。

根本原因は、読取専用GETを行う前に `publication` に `writeIntent=True` を立てたことで、読み取り失敗を「公開送信結果不明」と誤分類したこと。さらに `network_readiness` の成功済み診断でも `begin` を繰り返し、read-onlyチェックが同日3試行の上限を消費したこと。

対応（PR #5）:

1. 事前GET・公開receipt・他run確認・サイト生成物とGitHubの一致確認まで**送信記録なし**。正常なGETと事前検証の後、公開段階のbegin → 永続ジャーナル保存 → `writeIntent=True` checkpoint → `workflow_dispatch` を1回だけ実行。
2. ジャーナル保存後は、checkpoint失敗・通信失敗・受理応答喪失のいずれでも再dispatchしない。復旧タスクはGET観測に限定。元runのPublish Pages成功を検証するまで公開済みと記録しない。
3. `network_readiness` は書込みではないため、成功済みreadinessの再開確認では試行回数を増やさない。失敗からの読取確認も永続的な「3回書込み試行」制限を適用せず、同一プロセス3回・時刻窓・バックオフを維持。
4. PRIVATE `daily/logs/*.json` に、失敗GETの `resource` (`workflow_runs`/`workflow_run`/`workflow_jobs`/`other`)、`httpStatus` (HTTP 100〜599またはnull)、`exceptionType` (固定リスト) のみ記録。ログ・stdout・GitHubにAPI認証、フルURL、プロキシ情報、レスポンス本文、任意の例外メッセージを出さない。

**旧状態への注意:** 11/4に旧コードで記録された `writeIntent=True` / `unknown` を自動解除しない。日付・入力世代・本番公開可否の確認なく古い記事を再公開しない。現行の復旧時刻窓（06:10〜09:10）と商品鮮度を変えない。11/5の正規実行に切り替えるには、Windowsの`run_windows_daily.py`などを**PR #5反映後のmain**へ更新する必要がある。GitHubへのマージだけではWindowsタスクの実体は更新されない。

受入れ確認では、(a)事前GET失敗時のジャーナル不在・writeIntent=false・実dispatch0、(b) GET復旧後の当週正規データでの1回だけdispatch、(c) 永続意図後の不明状態で2度とdispatchしない、(d) 連続復旧確認でnetwork_readinessの3試行上限を踏まない、(e) 実本番のPublish Pages成功と公開された日付・内容の確認を分けて記録する。
