# Video Cross Search

YouTubeとニコニコ動画を同じ画面で扱う授業制作です。

## 初めて使う人・共有する人へ

**[共有・起動手順書](docs/SHARING.md)** に、公開リポジトリの共有、ZIP取得、Python環境の作成、YouTubeキーの設定、起動、困ったときの対処をまとめています。
各自のWindows PCで動かす手順です。GitHubのURLはソース共有用で、公開済みアプリのURLではありません。

## 今回の実装範囲

- Python + FastAPIで、固定の動画データ12本を返すバックエンド。
- YouTubeとニコニコが各6本、交互に並びます。
- ローカルの代替画像、API確認画面、フロント向け仕様書、テストを用意しています。
- **固定12本の表示に加え、YouTube・ニコニコ動画の横断検索を実装しました。**
- `video_url` は元サイトへのリンクです。クリックすると別タブで開きます。
- データベースやログインは不要です。YouTube検索のみAPIキーが必要です。未設定でもニコニコ検索は利用できます。
- 検索結果は各サイト最大6本。一方が失敗しても、取得できたサイトの結果を表示します。
- キー設定と検索処理の解説は [docs/SEARCH.md](docs/SEARCH.md) を参照してください。

## Windows / PowerShellで起動

Python 3.10以上が必要です。確認済みの環境はPython 3.10.11です。
最初にこのREADMEがあるフォルダをPowerShellで開いてください。

```powershell
# このプロジェクト専用のPython環境を作る（初回のみ）。
python -m venv .venv
# 動作確認時と同じバージョンのライブラリを入れる（初回・更新時）。
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
# バックエンドのフォルダへ移動する。
cd backend
# ローカルPCだけからアクセスできる開発サーバーを起動する。
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

仮想環境のactivateは不要です。停止はCtrl+Cです。
- カード一覧画面: http://127.0.0.1:8000/
- 動画一覧JSON: http://127.0.0.1:8000/api/videos
- 操作できるAPI説明画面: http://127.0.0.1:8000/docs
- 起動確認: http://127.0.0.1:8000/health

/docsでGET /api/videos → Try it out → Executeを押すと、12本分のJSONを確認できます。
API説明画面は外部CDNのスクリプトを使うため、オフラインでは表示できない場合があります。
一覧APIは外部ネットワークなしで動きますが、実動画のサムネイルとリンク先にはインターネット接続が必要です。

## データの差し替え

`backend/data/videos.json` を編集します。画像は `backend/static/` にあります。
JSONではコメントが使えません。項目説明は [docs/API.md](docs/API.md) を参照してください。
動画を変更する場合はID・タイトル・画像・動画URLをセットで差し替えます。選定した動画と手順は[動画一覧](docs/VIDEOS.md)を参照してください。
データは起動時に読み込むため、**JSONを変更したらサーバーを再起動**してください。
不正なJSON、未対応の投稿元、IDの重複、動画URLとIDの不一致があると起動に失敗します。
オフラインの表示確認には、残してある`backend/data/videos.sample.json`の内容を使えます。

## フロント担当との接続

[API仕様書](docs/API.md) と [frontend/README.md](frontend/README.md) を共有してください。
既定ではlocalhost/127.0.0.1の5173・5500番ポートを許可しています。
別のポートを使う場合は、バックエンドを起動するPowerShellで指定します。

```powershell
$env:FRONTEND_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
```

CORSはブラウザ向けの通信許可設定で、ログインやアクセス制限の代わりではありません。
別PCとの接続・一般公開用の設定は今回行っていません。

## テスト

backendフォルダから実行します。
```powershell
..\.venv\Scripts\python.exe -m pytest -q
```

固定一覧に加え、検索結果の統合・入力チェック・部分成功・タイムアウト・キャッシュを確認します。外部APIは模擬応答に置き換えるため、テストにAPIキーは不要です。
`requirements-lock.txt` はテスト用ライブラリを含む検証済みバージョンの一覧です。
依存関係を更新するときは `requirements-dev.txt` を使ってインストールし、テスト後にlockを更新します。

## ファイル構成

- `backend/app/main.py`: 固定データ読み込み・APIの入口。
- `backend/app/models.py`: 共通の動画形式と検証。
- `backend/app/search.py`: 外部検索・形式変換・交互表示・キャッシュ。
- `backend/data/videos.json`: 選定した実動画12本の固定データ。
- `backend/data/videos.sample.json`: オフライン確認用の旧仮データ。
- `docs/VIDEOS.md`: 掲載動画の出典と差し替え方。
- `backend/static/`: サンプル用の代替画像。
- `backend/tests/`: 外部APIを使わない動作確認。
- `frontend/`: HTML・CSS・JavaScriptのカード画面。
- `docs/API.md`: フロントとバックエンドの受け渡しルール。

一覧画面はAPIから取得し、PCで横4列×縦3行に表示します。画面を狭めると列数が減ります。
検索結果は0〜12本です。YouTubeのAPIキーを設定すると両サイトを検索できます。ページ送り、Twitch、共通の関連度ランキングは今回の範囲外です。

## ZIPで取得した旧版からの更新

起動中のPowerShellでCtrl+Cを押して停止してください。
GitHubから最新ZIPをダウンロードして**別フォルダへ展開**し、そのフォルダで上記の起動手順を実行します。
旧フォルダや編集済みの動画JSONは消さずに残してください。必要な変更だけ新しい版へ移してください。
画面は /api/videos ではなく http://127.0.0.1:8000/ で確認します。
ブラウザに古い表示が残る場合はCtrl+F5で再読み込みしてください。
