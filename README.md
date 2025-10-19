# Fumotoppara Watch (Starter)

このリポジトリは、ふもとっぱらの予約カレンダーを10分ごとに監視し、
対象セルが「〇 / △」になったら LINE Notify へ通知します。

## 使い方（超速）
1. このフォルダを GitHub にアップロード（*このREADMEを含むすべて*）
2. GitHub → Settings → Secrets and variables → **Actions** → New repository secret
   - 名前: `LINE_NOTIFY_TOKEN`、値: LINE Notify のトークン
3. Actions タブ → `Fumotoppara Watch` → **Run workflow**（手動で一度実行）
4. 以降は 10 分ごとに自動実行されます（UTC基準）。

## 監視対象の変更
- `TARGET_DATE_LABEL`（例: `"10/24"`）
- `TARGET_ROWS`（例: `"キャンプ宿泊,キャンプ日帰り"`）

は `.github/workflows/fumoto-watch.yml` 内の `env:` を編集してください。
