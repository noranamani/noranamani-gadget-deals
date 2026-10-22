# noranamani GADGET DEALS

楽天市場の商品比較・価格履歴・特価候補を掲載する静的サイトです。

Oracle停止を受けたWindows取得・GitHub Actions公開への移行コードを実装しています。現時点はレビュー用で、本番運用への切替は未承認です。

- 毎日06:10 JST: WindowsタスクからPythonを直接実行し、商品・価格履歴を同一世代で検証・反映。
- 毎日06:30 JST: 既存の編集タスクで日次コメントを更新。
- 日曜06:40 JST: 既存の編集タスクでnote編集JSONを更新。
- 毎日06:50 JST: 有効化後のActionsでサイト検証・Pagesデプロイ。日曜だけnote生成物をmainに保存。

移行手順、認証、非公開設定、編集タスクの変更案、切り戻し、未検証項目は [Windows移行手順](operations/windows-migration.md) を参照してください。今回、タスクやPages設定は変更していません。

取得カテゴリはconfig/queries.jsonから動的に読みます。値下がり判定は保存履歴との比較で、市場全体の最安値を示しません。履歴を切り捨てず、欠測日の価格を作りません。商品にない仕様は推測しません。掲載リンクには楽天アフィリエイトを使用します。

標準ライブラリのみのPython 3.12とGitを使用します。API認証情報はWindows資格情報マネージャーから取得し、コード・ログ・Gitに保存しません。TLS検証は常に有効です。アフィリエイト帰属確認が済むまで本番反映を止めます。

オフライン検証:

```powershell
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```

旧Oracle用シェル3本は誤実行を防ぐため停止します。引数なしfetch_rakuten.pyも動作しません。復旧時に古い処理を無条件で再開しないでください。
