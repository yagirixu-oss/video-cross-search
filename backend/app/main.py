"""固定動画一覧と、外部サイトをまとめた検索API。起動方法はREADME.mdを参照。"""
import json
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Query, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import httpx
from dotenv import dotenv_values
from .models import Video, VideoList
from .search import SearchService, SearchResponse, SearchTarget, SearchSort
from .pagination import CursorError
from .filters import SearchPeriod
from .history import HistoryStore, owner_from
from .history_api import history_router
from .personalization import PersonalSearch

# ── 固定ファイルの読み込み ───────────────────────────────
# 【根幹】コマンド実行場所ではなく、このファイルの位置からパスを求める。
BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


def load_catalog(path: Path) -> VideoList:
    """JSONの形式とID重複を検証。不備があれば起動時にエラーで知らせる。"""
    catalog = VideoList.model_validate(json.loads(path.read_text(encoding="utf-8")))
    ids = [video.id for video in catalog.videos]
    if len(ids) != len(set(ids)):
        raise ValueError("動画IDが重複しています。data/videos.jsonを確認してください。")
    return catalog


# ── API本体の組み立て ────────────────────────────────────
def create_app(data_path: Path | None = None, *, search_transport: httpx.AsyncBaseTransport | None = None,
               history_path: Path | None = None) -> FastAPI:
    """APIを作る。テスト時には別のデータファイルを指定できる。"""
    path = data_path if data_path is not None else BACKEND_DIR / "data" / "videos.json"

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """起動時に一度読み込む。壊れたデータを正常な空一覧として扱わない。"""
        application.state.catalog = load_catalog(path)
        # 環境変数を優先し、なければbackend/.envを読む。値をログに出さない。
        file_config = dotenv_values(BACKEND_DIR / ".env", encoding="utf-8-sig")
        youtube_key = os.getenv("YOUTUBE_API_KEY", file_config.get("YOUTUBE_API_KEY") or "").strip()
        # クライアントは起動中だけ再利用し、終了時に接続を閉じる。
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False,
            headers={"User-Agent": "video-cross-search-classroom/0.7"},
            transport=search_transport,
        ) as client:
            application.state.search_service = SearchService(client, youtube_key)
            application.state.history = HistoryStore(history_path or Path(os.getenv(
                "HISTORY_DB_PATH", str(BACKEND_DIR / "data" / "private" / "history.sqlite3"))))
            application.state.personal_search = PersonalSearch(application.state.search_service, application.state.history)
            yield

    application = FastAPI(title="Video Cross Search API", version="0.7.0", lifespan=lifespan)
    application.include_router(history_router())

    # ── 更新後の画面と処理が食い違わないようにする ──────
    @application.middleware("http")
    async def refresh_frontend(request: Request, call_next):
        """【根幹】HTML/CSS/JSを保存させず、古い条件付き要求による304も防ぐ。"""
        is_frontend = request.url.path == "/" or request.url.path.startswith("/ui/")
        if is_frontend:
            request.scope["headers"] = [
                (key, value) for key, value in request.scope["headers"]
                if key.lower() not in (b"if-none-match", b"if-modified-since")
            ]
        response = await call_next(request)
        if is_frontend or request.url.path.startswith(("/api/history", "/api/playback")):
            response.headers["Cache-Control"] = "no-store"
        return response

    # CORSは、別ポートで動く画面からのアクセスをブラウザに許可する設定。
    # ログインは未実装なので、Cookieの送受信は許可しない。
    origins = os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5500,http://127.0.0.1:5500",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        allow_methods=["GET"],
        allow_headers=[],
        allow_credentials=False,
    )
    application.mount("/static", StaticFiles(directory=BACKEND_DIR / "static"), name="static")

    @application.get("/api/videos", response_model=VideoList)
    def get_videos(request: Request) -> VideoList:
        """【根幹】画面の一覧取得依頼を受け、検証済みの動画情報をJSONで返す。"""
        return request.app.state.catalog

    # ── 検索の入口 ────────────────────────────────────
    @application.get("/api/search", response_model=SearchResponse,
                     responses={502: {"model": SearchResponse, "description": "要求したサイトで検索できなかった"},
                                410: {"description": "続きの情報が無効または期限切れ"}})
    async def search_videos(request: Request, response: Response,
                            q: str = Query(min_length=1, max_length=100),
                            cursor: str | None = Query(default=None, min_length=1, max_length=4096),
                            target: SearchTarget = Query(default="all"),
                            sort: SearchSort = Query(default="default"),
                            period: SearchPeriod = Query(default="all"),
                            personalized: bool = Query(default=False)) -> SearchResponse:
        """【根幹】入力検証 → 両サイトへ依頼 → 結果の統合。失敗と0件を区別する。"""
        query = q.strip()
        if not query or any(ord(char) < 32 or ord(char) == 127 for char in query):
            raise HTTPException(status_code=422, detail="検索キーワードを1〜100文字で入力してください。")
        try:
            if personalized:
                result = await request.app.state.personal_search.run(
                    owner_from(request), query, cursor, target, sort, period)
            else:
                result = await request.app.state.search_service.search(query, cursor, target, sort, period)
        except CursorError as exc:
            # 再起動・期限切れ・別キーワードのカーソルでは最初から再検索してもらう。
            raise HTTPException(status_code=410, detail=str(exc)) from None
        # 今回要求したサイトがすべて失敗した場合は502。対象外のサイトで成功を偽装しない。
        if not any(source.requested and source.status == "ok" for source in result.sources.values()):
            response.status_code = 502
        response.headers["Cache-Control"] = "no-store"
        return result

    @application.get("/health")
    def health() -> dict[str, str]:
        """サーバーが起動できているかを確認するための窓口。"""
        return {"status": "ok"}

    # ── 画面の配信 ─────────────────────────────────────
    # APIと同じサーバーで配信するので、追加の開発サーバーは不要。
    @application.get("/", include_in_schema=False)
    @application.get("/ui/index.html", include_in_schema=False)
    def index() -> HTMLResponse:
        """ファイル内容から版番号を作り、旧URLのキャッシュと区別する。"""
        # 更新日時が同じZIPでも、内容が変わればURLが変わる。手動の版上げは不要。
        assets = b"".join((FRONTEND_DIR / name).read_bytes() for name in ("app.js", "history.js", "styles.css"))
        version = hashlib.sha256(assets).hexdigest()[:16]
        template = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(template.replace("__ASSET_VERSION__", version))

    application.mount("/ui", StaticFiles(directory=FRONTEND_DIR), name="ui")

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        """ブラウザが自動要求するアイコンにも応答して404を防ぐ。"""
        return FileResponse(FRONTEND_DIR / "favicon.svg", media_type="image/svg+xml")

    return application


# Uvicornが「app.main:app」という指定で読み込む入口。
app = create_app()
