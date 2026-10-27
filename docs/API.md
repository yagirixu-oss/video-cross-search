# 動画一覧API v0.1

## リクエスト
`GET http://127.0.0.1:8000/api/videos`
入力パラメーター・認証は不要です。成功時は200とJSONを返します。

## レスポンス
```json
{
  "videos": [
    {
      "id": "youtube:sample-1",
      "platform": "youtube",
      "title": "【仮データ】YouTube サンプル 1",
      "thumbnail_url": "/static/youtube-placeholder.svg",
      "video_url": null
    }
  ]
}
```
上記は1本分の例です。実際は12本で、youtube / niconicoの順を6回繰り返します。

| 項目 | 型 | 用途 |
|---|---|---|
| id | 文字列 | 動画を区別する。実データでは投稿元:元サイトの動画ID |
| platform | youtube または niconico | 投稿元表示 |
| title | 文字列 | カードのタイトル。HTMLとして解釈せずテキスト表示 |
| thumbnail_url | 文字列 | 絶対URL、またはAPIサーバー基準の画像パス |
| video_url | HTTP(S) URL文字列 または null | 実動画へのリンク。nullは未選定でクリック不可 |

**補足：当初のURL案に対して、仮データだけはvideo_urlをnullにしています。**
有効な動画URLがあるように見せないためです。実動画の登録時はURLを設定します。

## 画面側の呼び出し例
これは接続の説明用コードで、完成したフロント実装ではありません。

```javascript
// ── バックエンドとの通信 ────────────────────────
const API_BASE = "http://127.0.0.1:8000";

// 動画一覧を取得する関数。失敗は呼び出し側の画面で表示する。
async function fetchVideos() {
  const response = await fetch(new URL("/api/videos", API_BASE));
  // 【根幹】fetchは404や500でも自動では例外にしないため、明示的に確認する。
  if (!response.ok) {
    throw new Error("動画一覧を取得できませんでした");
  }
  const data = await response.json();
  return data.videos;
}

// ── 画像URLの解決 ──────────────────────────────
// 相対パスを画面側のサーバーではなく、API側のURLに変換する。
function resolveThumbnail(video) {
  return new URL(video.thumbnail_url, API_BASE).href;
}
```

## 画面側で実装する状態
- 読み込み中は「読み込み中」を表示。
- 通信失敗時はエラーと再試行ボタンを表示。
- 配列の順番を保ち、PCで4列×3行に配置。
- video_urlがnullならリンクを無効にし「準備中」を表示。
- 実URLは元サイトへ遷移。別タブならrel="noopener noreferrer"を設定。
- 画像が読めない場合は代替表示。
- 返却配列が空の場合にも「動画がありません」と表示できる構成にする。

## エラーと制約
- 存在しないパスは404、POSTなど未対応メソッドは405。
- 固定データに不備がある場合は起動時エラー。画面には通信失敗として扱ってもらう。
- /healthは200で {"status":"ok"} を返す。
- 検索・ページ送り・外部サービス連携はまだ未実装。
- APIキーや秘密情報は返却しない。将来導入時もバックエンドで管理する。
