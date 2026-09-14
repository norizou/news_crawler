# 貢献ガイドライン (Contributing Guide)

`ai_scraper` への貢献（Issue 報告、機能提案、Pull/Merge Request）を歓迎します。

---

## 開発環境のセットアップ

1. **リポジトリのクローン**:

   ```bash
   git clone <repository_url> ai_scraper
   cd ai_scraper
   ```

2. **依存関係のインストール**:

   ```bash
   uv sync
   uv run playwright install chromium
   ```

3. **ドキュメント Lint 環境のセットアップ**:

   ```bash
   npm ci
   ```

---

## テストと静的解析の実行

コミット前に以下のコマンドを実行し、すべてのチェックが通過することを確認してください。

```bash
# ドキュメント Lint (Markdown & textlint)
npm run lint

# Python 静的解析 (Ruff)
uv run ruff check .

# Python 型チェック (Mypy)
uv run mypy src

# 単体テスト (Pytest)
uv run pytest
```

---

## コミット規約

コミットメッセージは [Conventional Commits](https://www.conventionalcommits.org/) に準拠することを推奨します。

- `feat:` 新機能の追加
- `fix:` バグ修正
- `docs:` ドキュメントの変更
- `refactor:` リファクタリング
- `test:` テストの追加・修正
- `chore:` ビルド設定や依存関係の更新
