# 動画一覧・検索API v0.7

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
      "video_url": null,
      "published_at": null,
      "view_count": null,
      "author_name": null,
      "author_id": null,
      "metadata_fetched_at": null
    }
  ]
}
```
上記は仮データの例です。現在の既定データは実動画12本で、video_urlに元サイトのURLが入ります。youtube / niconicoの順を6回繰り返します。

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
これは接続の説明用コードです。実装済みの画面はfrontend/app.jsを参照してください。

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
- 外部検索と続き取得は以下の /api/search を使用。
- APIキーや秘密情報は返却しない。将来導入時もバックエンドで管理する。

## 同じサーバーでの画面配信
GET / はカード画面、/ui/ はフロントのCSS・JavaScript・アイコンを返します。
frontend/app.jsは相対URL /api/videos を使用するため、追加のCORS設定は不要です。

## 実動画リンクの検証
video_urlがnullでない場合は、HTTPSの正規URL形式を使用してください。
- YouTube: https://www.youtube.com/watch?v=動画ID
- ニコニコ: https://www.nicovideo.jp/watch/動画ID

投稿元・ID・URLが一致しないデータは起動時に拒否します。短縮URLは正規URLへ直して登録してください。
サムネイルは外部の画像URLを参照します。APIは外部画像や動画ファイルを代理配信しません。


## 横断検索

`GET /api/search?q=初音ミク`

qは必須。1〜100文字で、前後の空白を除去します。空白のみ・制御文字は422です。
動画の形式は固定一覧と同じです。入力語は両サイトへ送信します。

```json
{
  "query": "初音ミク",
  "videos": [],
  "sources": {
    "youtube": {"status": "unavailable", "count": 0, "code": "not_configured", "message": "YouTube検索はまだ設定されていません。ニコニコ動画のみ検索しています。", "cached": false},
    "niconico": {"status": "ok", "count": 0, "code": null, "message": null, "cached": false}
  },
  "partial": true
}
```

上記はYouTube未設定でニコニコが0件だった場合の例です。

| 項目 | 意味 |
|---|---|
| query | 前後の空白を除いた検索語 |
| videos | 通常は1回につき各サイト最大6本、合計最大12本。履歴補正時は各18本、合計最大36本。YouTubeから交互。片方が少ないと残りを続ける |
| sources | 各サイトの状態。okは成功（0件を含む）、unavailableは未設定、errorは失敗 |
| count | そのサイトから今回表示する動画数。検索対象全体の総件数ではない |
| code / message | エラー分類と画面用メッセージ。成功時はnull |
| cached | サーバー内の60秒キャッシュから返したときtrue |
| partial | いずれかのサイトがokでないときtrue。両方失敗でもtrue |

HTTP 200は今回要求したサイトのうち少なくとも一つが成功した場合、502は要求したサイトで一つも成功しなかった場合です。失敗サイトだけの再試行も同じ判定です。
**画面は502でもJSONを読み、sourcesの理由を表示してください。** 422はFastAPI標準のdetail形式です。
0件の成功を通信失敗として扱わないでください。

主なcodeはnot_configured、configuration_error、quota_exceeded、timeout、connection_error、
busy、rate_limited、cooldown、query_rejected、upstream_error、invalid_responseです。
キーや外部サービスの生エラー本文は返しません。

標準設定のYouTubeは関連度順の候補6件からライブ・配信予定等を除き最大6件、ニコニコはタイトル・説明・タグを検索して再生数順に最大6件です。
サイトをまたぐ関連度の比較は行いません。ニコニコは日次スナップショットのため最新投稿がすぐ反映されない場合があります。
外部APIの制限や動画の削除により、常に12本になるとは限りません。

同じ語の成功結果はサーバー内で60秒保持し、全サイト合計最大128検索分に制限します。
ブラウザ等への応答はCache-Control: no-storeです。ニコニコの503後は5分待機、429後は最低60秒待機します。
サイトごとの処理は待機を含め16秒で打ち切ります。詳細と公式資料は [SEARCH.md](SEARCH.md) を参照してください。


## 続き取得（v0.3で追加）

初回は `GET /api/search?q=検索語`、続きは `GET /api/search?q=同じ検索語&cursor=next_cursor`。
パラメーターはURLSearchParams等でURLエンコードしてください。APIが返した文字列をそのまま使い、画面側で分解・生成しないでください。

| 追加項目 | 意味 |
|---|---|
| next_cursor | 次回の取得位置。nullなら続きなし。失敗したサイトは失敗時の位置を保持する |
| retry_cursor | 失敗サイトだけを再試行するための位置。nullなら再試行対象なし |
| sources.*.requested | 今回そのサイトを検索対象にしたか。終了済み・再試行対象外はfalse |
| sources.*.has_more | 続きを取得できるか。失敗時は同じ位置での再試行が可能なためtrue |
| sources.*.end_reason | exhaustedは終了、offset_limitはニコニコの上限、その他はnull |

sources.*.countは今回返した件数で、画面の累計ではありません。フロントはIDで重複を除き、表示中の動画から累計を計算します。
requested=falseのサイトは、画面が保持する以前の状態を上書きしないでください。
部分失敗時はretry_cursorで失敗サイトだけを再試行でき、next_cursorでは成功サイトの続きと失敗サイトの再試行を同時に行えます。
どちらもレスポンスを受け取ったら、返された新しいカーソルへ置き換えます。

カーソルは検索語に紐づく署名付き文字列で、有効期限は発行から30分です。再起動でも無効になります。
期限切れ・改ざん・別の検索語では410とdetailメッセージを返し、外部APIは呼びません。
空文字または4096文字超のcursorは422です。410時は既存一覧を残し、ユーザー操作で再検索します。
キャッシュのキーにはサイト・検索語・ページ位置を含みます。同じページの再送では位置を勝手に進めません。
YouTubeはmaxResults=6とpageToken、ニコニコは_limit=6と_offsetを使用します。
ニコニコは元のdata件数で_offsetを進め、meta.totalCountで終了を判断します。100000を超えるoffsetは要求しません。

## 検索対象（v0.4で追加）

`GET /api/search?q=検索語&target=youtube`

targetはall（既定・両方）、youtube、niconicoのいずれかです。それ以外は422で外部通信しません。
レスポンスのtargetにも実行した対象を返します。対象外サイトはstatus=skipped、selected=false、requested=false、count=0です。
対象外サイトへ外部通信は行わず、キャッシュの動画も返しません。partialの判定からも対象外を除きます。
対象内で結果が尽きた場合はselected=true、requested=falseなので、対象外と区別できます。
YouTube未設定でもniconico指定は正常に検索でき、YouTubeの未設定エラーは返しません。
youtube指定でキーがなければ502とnot_configuredを返し、ニコニコへ代替検索しません。

続き・再試行にも、最初と同じtargetとqを送ってください。カーソルは検索対象にも紐づき、異なるtargetでの使用は410です。
同じサイト・キーワード・位置・並び順のキャッシュは、allと単一サイト検索の間で共有できますが、応答には選択したサイトだけを含めます。


## 並び順（v0.5で追加）

`GET /api/search?q=検索語&target=all&sort=newest`

| sort | 画面表示 | YouTube order | ニコニコ _sort |
|---|---|---|---|
| default（省略時） | 標準 | relevance | -viewCounter |
| newest | 新しい順 | date | -startTime |
| views | 再生数が多い順 | viewCount | -viewCounter |

レスポンスのsortにも実行した並び順を返します。未対応の値は422で外部通信しません。
各サイトの応答順序を保って交互表示します。両サイト全体の日時・再生数による総合ソートは行いません。
画面内の取得済み動画だけを並べ替える処理ではなく、外部APIに並び順を指定して先頭から検索します。
続き・再試行には同じq・target・sortを指定してください。別sortのカーソルは410です。
キャッシュもsortを含めて区別し、別順序の結果を混ぜません。
YouTubeでは並び順によって取得できる結果の範囲が変わる場合があります。新しい順でも検索インデックスの反映遅延があり、ニコニコも日次スナップショットです。

公式資料：[YouTube search.list](https://developers.google.com/youtube/v3/docs/search/list)、[ニコニコ検索API](https://site.nicovideo.jp/search-api-docs/snapshot)。


## 追加情報と投稿期間（v0.6で追加）

`GET /api/search?q=初音ミク&target=all&sort=newest&period=week`

| period | 意味 |
|---|---|
| all | 既定。期間指定なし |
| week | 最近7日 |
| month | 最近30日（暦月ではない） |
| year | 最近365日（暦年ではない） |

不正なperiodは422。期間は初回検索のUTC時刻を分単位に丸めて計算します。
応答にperiod、published_after、published_beforeを返し、指定なしなら日時は両方nullです。
続き・再試行には同じq/target/sort/periodと、受け取ったcursorを送ります。
期間と日時範囲も署名に含めます。途中でperiodを変えると410です。キャッシュは日時範囲ごとに分離します。
動画の公開日時による絞り込みであり、「最近視聴された動画」の絞り込みではありません。

固定一覧と検索結果のVideoに次を追加しました。旧JSONで省略された項目はnullになります。

| 項目 | 型 | 意味 |
|---|---|---|
| published_at | 日時文字列 または null | タイムゾーン付き公開日時。画面は日本時間で表示 |
| view_count | 0以上の整数 または null | 取得時点の再生数。nullを0に置き換えない |
| author_name | 文字列 または null | 投稿者・チャンネルの表示名 |
| author_id | 文字列 または null | 名前がない場合の識別用。ニコニコはuser:IDまたはchannel:ID |
| metadata_fetched_at | 日時文字列 または null | この追加情報を取得した時刻 |

sources.*.metadata_warningは追加情報だけの取得不備を知らせる文字列、正常ならnullです。
この警告だけではstatusをerrorにせず、検索動画・next_cursorを返します。retry_cursorは検索自体の失敗用です。
再生数などの不足分を取り直すには、60秒のキャッシュ期限後に同じ条件で再検索します。
外部検索インデックスと詳細情報の更新時差により、表示した再生数の厳密な降順にならない場合があります。
## v0.7の履歴機能

`GET /api/search` に `personalized`（真偽値、既定false）を追加しました。
trueの場合は `X-History-Token` が必要です。詳しい窓口・保存・取得制限は[履歴仕様](HISTORY.md)を参照してください。
レスポンスに `personalized`, `personalization_note`, `preference_reasons`, `max_per_site` を追加しています。
通常は各サイト6件、履歴による標準順の補正時は18件です。
個人向けのカーソルも不透明な文字列としてそのまま返してください。他の利用者との共有はできません。

