# twitter-fetcher

X (Twitter) の特定アカウントの最新ツイートを取得し、Discord へ自動投稿するツール。

GitHub Actions で毎朝 07:00 JST に自動実行されます。

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、値を設定します。

```bash
cp .env.example .env
```

```
X_BEARER_TOKEN=your_bearer_token_here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXXXX/XXXXX
```

- `X_BEARER_TOKEN`: [X Developer Portal](https://developer.twitter.com/) で発行した Bearer Token
- `DISCORD_WEBHOOK_URL`: Discord チャンネルの設定 → 連携サービス → ウェブフック で発行した URL

### 3. 監視アカウントの設定

`accounts.txt` に監視したいアカウントのハンドルを記述します（1行1アカウント）。
このファイルは `.gitignore` で除外されているため、リポジトリには含まれません。

```
# コメント行は無視されます
@OpenAI
anthropic
```

`@` プレフィックスはあってもなくても動作します。

---

## 使い方

### Discord への自動投稿（メインスクリプト）

```bash
# 昨日のツイートを取得して Discord に投稿
python post_tweets.py

# 動作確認（Discord に投稿しない）
python post_tweets.py --dry-run

# 日付を指定して実行
python post_tweets.py --date 2024-06-15

# リプライ・リツイートも含める
python post_tweets.py --include-replies --include-rts
```

**オプション一覧:**

| オプション | デフォルト | 説明 |
|---|---|---|
| `--accounts` | `accounts.txt` | アカウントリストのパス |
| `--cache` | `user_cache.json` | ユーザー ID キャッシュのパス |
| `--seen` | `seen_tweets.json` | 投稿済みツイート ID の記録ファイル |
| `--date` | `yesterday` | `yesterday` または `YYYY-MM-DD`（JST） |
| `--hours` | なし | 直近 N 時間分を取得（指定すると `--date` より優先） |
| `--max` | `20` | 1アカウントあたりの最大取得件数（1〜100） |
| `--include-replies` | off | 全リプライを含める |
| `--include-self-replies` | off | 自分自身へのリプライ（スレッド）のみ含める |
| `--include-rts` | off | リツイートを含める |
| `--api-base` | `https://api.x.com/2` | X API のベース URL |
| `--dry-run` | off | Discord に投稿せず標準出力に表示 |

### テキスト出力のみ（サブスクリプト）

Discord 投稿不要でツイートを Markdown テキストとして出力したい場合:

```bash
python xsum_api.py --accounts accounts.txt
```

---

## GitHub Actions による自動実行

### 設定

リポジトリの **Settings → Secrets and variables → Actions** に以下を登録します:

| シークレット名 | 値 |
|---|---|
| `X_BEARER_TOKEN` | X API の Bearer Token |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL |
| `ACCOUNTS_TXT` | 監視アカウントの一覧（改行区切り、例: `@OpenAI\nanthropic`） |

### スケジュール

**6時間ごと**（03:00 / 09:00 / 15:00 / 21:00 JST）に自動実行されます。
直近7時間分のツイートを取得し、`seen_tweets.json` で重複を除外して投稿します。

手動実行は Actions タブ → "Daily Tweets" → **Run workflow** から可能です。

### キャッシュ

GitHub Actions の Cache 機能で以下のファイルを実行間で引き継ぎます:

- `seen_tweets.json` — 投稿済みツイート ID（重複投稿防止、30日で自動削除）
- `user_cache.json` — アカウントのユーザー ID（API 呼び出し削減）

---

## X API の料金・上限

このツールは X API v2 を Bearer Token（アプリ認証）で使用します。

**2026年2月に無料プランは廃止され、新規登録はデフォルトで従量課金になりました。**

| プラン | 読み取り単価 | 月間上限 |
|---|---|---|
| 従量課金（新デフォルト） | $0.005 / ツイート | 200万読み取り |
| Enterprise | 要交渉 | 〜$42,000/月〜 |

### 月額コストの目安

1日5ツイート・1アカウントを毎日取得する場合：

5ツイート × 30日 = **150読み取り/月 → 約 $0.75（約110円）**

実際のツイート数が少なければその分だけ課金されます（`--max` で指定した上限まで取得しますが、ツイートがなければ課金もありません）。

既存の Bearer Token を使用している場合は、[X Developer Portal](https://developer.twitter.com/) のダッシュボードで現在の契約プランを確認してください。

---

## ファイル構成

```
twitter-fetcher/
├── post_tweets.py               # メインスクリプト（取得 + Discord 投稿）
├── discord_poster.py            # Discord Webhook 投稿モジュール
├── xsum_api.py                  # ツイート取得コアロジック（テキスト出力）
├── xfig_api.py                  # ツイート取得 + 画像カード生成
├── accounts.txt                 # 監視アカウント一覧（要編集）
├── requirements.txt             # pip 依存パッケージ
├── .env.example                 # 環境変数テンプレート
└── .github/workflows/
    └── daily-tweets.yml         # GitHub Actions ワークフロー
```
