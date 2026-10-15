# note下書き→自動公開への移行ゲート

## 現在
- 3サイト共通の投稿候補分類: `scripts/plan_note_queue.py`（dry-run）
- SQLite投稿履歴: `scripts/note_draft_ledger.py`（noteへの接続なし）
- GitHub Actions: `Three-site note queue dry-run`（投稿なし）
- noteブラウザ投稿機能、セッション維持、定期運用: **未実装**

## Oracle VMでの予定ディレクトリ（まだ作成しない）
```
/srv/note-pipeline/
  repos/{gadget,local,gacha}/
  state/note-posts.sqlite
  state/local-place-snapshot.json
  state/gacha-store-snapshot.json
  browser-profile/ (chmod 700; GitHubには絶対送信しない)
  logs/
```
- VM上で実行するユーザーを分離し、プロフィールやDBをバックアップ、排他ロックと障害通知を設定する。
- 既存note投稿バッチv0.3のコード・入出力・利用条件を確認後、Playwright等のブラウザ保存処理を移植。
- 初回認証は手動。追加認証やCAPTCHAが出たら停止。自動突破しない。
- 初めは1記事だけ下書き登録してブラウザとnote画面で確認する。
- 投稿IDと編集URLはブラウザ画面から取得した値を検証して記録する。保存結果が不明なら `uncertain` として再投稿しない。
- 週次記事生成と投稿は別ジョブとし、記事生成が失敗した週に古い記事を再送信しない。
- まず週次の下書き保存を2〜3週間観察する。
- 自動公開切り替えは下書き結果だけでは判断しない。出典・日付・重複・内容の品質、note利用条件、事故時停止の確認を満たすこと。公開機能は別フラグで既定OFF。
- ここまでの作業ではnoteアカウントへのログインや投稿はしていない。

## 手元からできる確認
GitHub > Actions > Three-site note queue dry-run > Run workflow。
GitHubのArtifactにJSONレポートを保存する。セッション情報は保存しない。

## 既存システムとの互換性
ガジェットは従来の `note/latest/article.md` があり、manifest未生成なら `needs_manifest` のまま投稿候補としない。既存バッチを変更しない。
地域とガチャは初回スナップショットとの差分がない場合 `skip`。定期ジョブ化にはVMでの状態永続化が必要。
