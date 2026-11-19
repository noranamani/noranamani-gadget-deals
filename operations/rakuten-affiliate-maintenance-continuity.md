# 楽天ウェブサービス UI ポータル停止：Affiliate ID 暫定継続

対象：2026-11-09からの楽天ウェブサービス管理画面停止。楽天公式では
**既存アプリのAPIは継続利用可能**と案内。管理画面復旧は未定。

公式告知: https://rakuten-webservice.tumblr.com/

## 設計と適用範囲

- **通常の30日本人確認記録を変更しない。** `state/affiliate-proof.json` は読み取りのみ。
- Windows資格情報マネージャー上の登録済みApplication ID + Affiliate IDと、
  既存の本人確認記録のbinding一致を必須とする。
- 同じ**非公開stateディレクトリ**に、オペレーターが対話式に1回だけ
  `state/affiliate-maintenance.json` を作成する。既存ファイルは上書き・延長しない。
- 期限は元の本人確認記録の失効時刻 + **最大7日**で固定。
  現行記録では **2026-11-27 14:37 JST**。
- 元の確認記録が有効な間は必ず通常モード。失効後だけmarkerを使う。
- 暫定時、collection-status.jsonへ `affiliateVerificationMode=maintenance_continuity`,
  `affiliateOriginalProofExpiresAt`, `affiliateContinuityAuthorizedAt` を保存し、
  statusの期限は**暫定許可期限**とする。元の確認記録が再認証されたと装わない。
- Cloud Pages側も別途「元の失効から7日以内・保存された承認日付より後」
  を検証。単にexpiresAtを書き換えて延長するのは拒否。
- 資格情報変更、確認記録/marker不整合、7日満了、API検証・秘密値検証失敗、
  ソース世代/価格履歴/Git整合失敗などは従来通り停止。API回数・タイムスロット・
  タスクスケジューラの設定・note.com投稿を変更しない。
- 復旧したら公式画面を再確認し、従来の`--verify-affiliate`で通常の新規確認記録を
  作る。新記録が有効な場合、古いmarkerは無視される。
- この実装は楽天API認証の期限切れを延長するのではなく、**自作サイト側の
  本人確認ルールを限定的に例外扱い**する運用変更。楽天が収益帰属を保証するものではない。

## 必要なローカル操作（GitHubだけでは稼働しない）

**1.** ローカルCodexでGitHub `main` の変更を、既存の版管理された
`GadgetPipeline/windows-production/code/<sourceCommit>/` 配置へ
安全に反映する。作業用チェックアウトはDドライブを使い、現行の
`code-source.json` とタスクの参照コードを照合する。
本番タスクの再登録・有効化変更、ログや確認記録の消去は行わない。
全オフラインテストを新しいソースで実行し、切替後に
`windows_credentials.py --check`で資格情報が読めることを確認する。
このチェックはAPI通信なし。

**2.** 楽天管理画面に接続できないこと、IDを変更していないことを本人が確認した上で、
通常ユーザーのPowerShellで以下を**1回だけ**実行：

```powershell
$pipelineHome = Join-Path $env:USERPROFILE 'GadgetPipeline\windows-production'
$source = Get-Content (Join-Path $pipelineHome 'code-source.json') -Raw | ConvertFrom-Json
$script = Join-Path $pipelineHome ("code\" + $source.sourceCommit + "\scripts\windows_credentials.py")
$proof = Join-Path $pipelineHome 'state\affiliate-proof.json'
& 'C:\Python312\python.exe' -X utf8 $script --authorize-maintenance $proof
```

実際の確認画面のAffiliate IDは入力できないため、代わりに
大文字の`MAINTENANCE`のみを入力して、この期限限定例外を本人が明示承認。
**Affiliate ID自体やアクセスキーを入力・転記しない。**

正常時は `{"affiliateMaintenance":"authorized","expiresAt":"2026-11-27T05:37:21...+00:00"}`
に相当する結果（実際は元記録の端数を継承）が表示される。
同一のmarker作成コマンドの再実行は拒否する。

**3.** 確認のみ（APIリクエスト／GitHub pushなし）：

```powershell
& 'C:\Python312\python.exe' -X utf8 $script --check-affiliate-proof $proof
```

元の本人確認が有効な間は`valid`、11月20日14:37以降は
`maintenance_continuity`と11月27日の期限を表示する。
**有効期限を試験するためにWindowsの時計を変えない。**

## ロールバック

暫定継続を取り消すには、ローカル本人が
`state/affiliate-maintenance.json`を安全に別名退避して、
新しい本人確認記録がなければ次回の更新がfail-closedで停止することを確認。
ソースコードや公開済みデータを巻き戻す必要はない。
現行の公開済みPagesや既存アフィリエイトURLがこのmarkerのみで失効することはない。

## 自動テストと未確認

`python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v`

テストは実楽天APIやnote.com、Windows資格情報を呼ばない。GitHubのCI成功は
**ローカルPCのコード切替・本人によるmarker作成・翌朝の実収集・実際の
Pagesデプロイ**を保証しない。各フェーズは別々に記録する。
