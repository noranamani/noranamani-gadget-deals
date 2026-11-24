# 2026-11-24 楽天UIポータル停止の追加対応（レビュー案）

公式告知 https://affiliate.rakuten.co.jp/info/ （2026-11-24）では登録済みアプリのAPI継続、次回案内を12月15日までとしています。これは個別のAffiliate IDの所有確認ではありません。通常の本人確認記録は書き換えません。

- 現行11月27日14:37 JSTまでの許可はそのまま。既存証跡とv1 markerを再検証し、別ファイルに人が明示承認した場合だけ12月15日末まで継続可能。
- 新規承認は11月25日から旧期限前まで。対象は現行の元証跡の期限が一致するものだけ。旧markerも更新しない。
- 新しい通常確認が取得できれば通常モードへ戻る。公式告知が変われば期限前でも見直す。
- Windowsコードとクラウド側検証を一致させる。API・公開・noteの実行や鮮度・重複防止は変更しない。
- PRだけでは許可も期限変更も発生しない。CI成功、運用承認、ローカルコード切替、本人の対話承認が必要。

## ローカル操作（レビュー・承認・main反映後のみ）

既存タスク、資格情報、stateを変えず、Dドライブの作業用チェックアウトから現行のバージョン付き配置へ承認済みmainを切り替える。全テストと資格情報の読取確認を実施。公式画面が回復していれば通常の --verify-affiliate を使用。

旧期限前に通常ユーザーのPowerShellで、現行のコード版に対して次を実行する。

    $homeDir = Join-Path $env:USERPROFILE 'GadgetPipeline\windows-production'
    $source = Get-Content (Join-Path $homeDir 'code-source.json') -Raw | ConvertFrom-Json
    $script = Join-Path $homeDir ("code\" + $source.sourceCommit + "\scripts\windows_credentials.py")
    $proof = Join-Path $homeDir 'state\affiliate-proof.json'
    & 'C:\Python312\python.exe' -X utf8 $script --authorize-maintenance-20261124 $proof
    & 'C:\Python312\python.exe' -X utf8 $script --check-affiliate-proof $proof

本人が画面停止とID未変更を確認し、必要な場合だけ MAINTENANCE-20261124 と入力。期限は2026-12-16 00:00 JST。秘密値はチャットやログに出さない。次回通常収集でpublic statusに別モードと承認時刻が記録される。

撤回は新markerのみ安全に退避。旧期限を過ぎて通常確認もなければ更新は安全停止する。旧証跡・旧markerを消さない。
