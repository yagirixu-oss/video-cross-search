# 横断検索の仕組みとYouTubeの設定

## 何を実装したか

1. 自分たちの検索窓口 `GET /api/search?q=検索語`。
2. その窓口からYouTubeとニコニコの公式APIを呼ぶ処理。
3. 異なる戻り値を共通の動画形式に変換し、1回につき各サイト最大6本を交互に並べる処理。
4. 検索欄と、読み込み中・結果・0件・一部失敗・全失敗の画面表示。

利用者が検索すると「画面 → 自分たちのFastAPI → 各サイトのAPI」の順に依頼が進みます。
返事は逆に戻ります。自分たちでYouTube全体の検索エンジンや動画データベースを作るわけではありません。
自分たちのAPIは、複数サイトへの依頼と、画面が扱いやすい形式への変換を担当する窓口です。
APIキーもこの窓口のあるサーバー側だけで使用します。

## YouTube APIキーを設定する

キーなしでも固定一覧とニコニコ検索は動きます。YouTube検索も使う場合に設定してください。

1. [Google公式の開始手順](https://developers.google.com/youtube/v3/getting-started)に従い、Google Cloudのプロジェクトで **YouTube Data API v3** を有効にし、APIキーを作成します。
2. キーのAPI制限をYouTube Data API v3に限定してください。このアプリはPythonサーバーから接続するため、ブラウザの参照元URLを前提にしたキー制限では接続できません。
3. このREADMEのあるプロジェクトのルートで、次を実行します。既存の.envがある場合はコピーせず、それを編集してください。

```powershell
# ── 設定ファイルを用意する（初回のみ） ─────────────────
# .env.exampleは説明用。実際に読み込むファイル名は.env。
Copy-Item backend/.env.example backend/.env
notepad backend/.env
```

開いたファイルの `YOUTUBE_API_KEY=` の右側に、取得したキーだけを記入して保存します。
キーはチャットに送らず、GitHubにもアップロードしないでください。.envは.gitignoreで除外しています。
`.env.example` は空欄のまま共有します。

4. サーバーをCtrl+Cで停止して起動し直し、画面から検索します。設定ファイルの変更は再起動で読み込みます。

`.env` の場所は **backend/.env** です。Windowsで `.env.txt` になっていないか確認してください。
PowerShell等で `YOUTUBE_API_KEY` 環境変数を設定している場合は、そちらが.envより優先されます。
利用枠や使用量はGoogle Cloudのコンソールで確認してください。枠超過時は画面に理由を表示します。

## コードを読む順序

- `app/main.py`：APIの入口。検索語を検証し、検索サービスに仕事を渡します。
- `app/search.py` の `search`：両サイトを並行して呼び出し、交互に並べます。
- 同ファイルの `_fetch`：HTTP通信で公式APIに依頼します。
- `parse_youtube` / `parse_niconico`：公式APIごとのデータを共通のVideoへ変換します。
- `app/models.py`：Videoの項目・型・URLのルールを定義します。
- `frontend/app.js`：返されたJSONからカードと案内を作ります。

「並行」は片方の返事を待つ間にもう片方にも依頼することです。
「キャッシュ」は直前の結果を短期間保存し、同じ依頼を繰り返さず返す仕組みです。
今回は成功した検索を60秒だけメモリーに保存します。再起動すると消えます。
キー不足や片方の障害で全部の検索が止まらないよう、サイト別に成功・失敗を扱います。

## 今回の範囲と制約

標準設定ではYouTubeは関連度順、ニコニコは再生数順です。新しい順・再生数が多い順も選べます。同じ順位基準ではないため、画面にも違いを表示しています。
今回の「平等」は各サイトの上限を6本に揃えて交互に表示することを指し、共通の関連度ランキングは未実装です。
片方に結果が少ない場合、もう片方で12本まで埋める処理は行いません。
YouTubeのライブ中・配信予定は除外します。Twitchは今回の範囲外です。
ニコニコの検索対象は日次スナップショットで、サイト本体の最新検索結果と一致する保証はありません。

この版はローカルの授業確認用です。アクセス間隔やキャッシュはサーバープロセス内で管理します。
複数プロセス・一般公開への展開時には、共有の利用制限とキャッシュ管理を別途設計します。

## 参照した公式資料

- [YouTube search.list](https://developers.google.com/youtube/v3/docs/search/list)
- [YouTube検索結果の形式](https://developers.google.com/youtube/v3/docs/search)
- [Google APIキーの使用方法](https://docs.cloud.google.com/docs/authentication/api-keys-use)
- [ニコニコ動画スナップショット検索API](https://site.nicovideo.jp/search-api-docs/snapshot)

2026-11-10確認。APIの条件や利用枠は変更されるため、公開前には改めて公式資料を確認してください。


## v0.6の補足

2026-11-24に[YouTube videos.list](https://developers.google.com/youtube/v3/docs/videos/list)と検索APIの公開日時条件、ニコニコ検索APIのstartTimeフィルターを確認しました。追加情報取得・通信回数・キャッシュ対策は [SEARCH-DETAILS.md](SEARCH-DETAILS.md) を参照してください。
