# Video Cross Search

YouTubeとニコニコ動画を同じ画面で扱う授業制作です。

## 今回の実装範囲

- Python + FastAPIで、固定の動画データ12本を返すバックエンド。
- YouTubeとニコニコが各6本、交互に並びます。
- ローカルの代替画像、API確認画面、フロント向け仕様書、テストを用意しています。
- **12本とも仮データです。実際の動画取得と検索は未実装です。カード画面は実装済みです。**
- 実動画を選ぶまで `video_url` は `null`。架空の動画URLへ誘導しません。
- データベース、ログイン、いいね、保存、外部APIキーはまだ不要です。

## Windows / PowerShellで起動

Python 3.10以上が必要です。確認済みの環境はPython 3.10.11です。
最初にこのREADMEがあるフォルダをPowerShellで開いてください。

```powershell
# このプロジェクト専用のPython環境を作る（初回のみ）。
python -m venv .venv
# 動作確認時と同じバージョンのライブラリを入れる（初回のみ）。
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
一覧APIと代替画像は外部ネットワークなしで動きます。

## データの差し替え

`backend/data/videos.json` を編集します。画像は `backend/static/` にあります。
JSONではコメントが使えません。項目説明は [docs/API.md](docs/API.md) を参照してください。
実際の動画を選んだらID・タイトル・画像・動画URLを差し替えます。
データは起動時に読み込むため、**JSONを変更したらサーバーを再起動**してください。
不正なJSON、未対応の投稿元、IDの重複があると、誤った一覧を返す代わりに起動に失敗します。

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

一覧の件数・順序・項目、画像、CORS、壊れたデータの検知を確認します。
`requirements-lock.txt` はテスト用ライブラリを含む検証済みバージョンの一覧です。
依存関係を更新するときは `requirements-dev.txt` を使ってインストールし、テスト後にlockを更新します。

## ファイル構成

- `backend/app/main.py`: データ形式・読み込み・APIの入口。
- `backend/data/videos.json`: 手作業で用意する固定データ。
- `backend/static/`: サンプル用の代替画像。
- `backend/tests/`: 外部APIを使わない動作確認。
- `frontend/`: HTML・CSS・JavaScriptのカード画面。
- `docs/API.md`: フロントとバックエンドの受け渡しルール。

一覧画面はAPIから取得し、PCで横4列×縦3行に表示します。画面を狭めると列数が減ります。
実動画12本の選定・リンク確認・画面の調整後、外部検索APIを接続します。

## ZIPで取得した旧版からの更新

起動中のPowerShellでCtrl+Cを押して停止してください。
GitHubから最新ZIPをダウンロードして**別フォルダへ展開**し、そのフォルダで上記の起動手順を実行します。
旧フォルダや編集済みの動画JSONは消さずに残してください。必要な変更だけ新しい版へ移してください。
画面は /api/videos ではなく http://127.0.0.1:8000/ で確認します。
ブラウザに古い表示が残る場合はCtrl+F5で再読み込みしてください。
