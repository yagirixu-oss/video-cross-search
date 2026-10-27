"""固定の動画一覧を返すAPI。起動方法はREADME.mdを参照。"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

# ── 画面と受け渡すデータ形式 ─────────────────────────────
class Video(BaseModel):
    """カード1枚の情報。未選定の仮動画にはリンクを設定しない。"""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, description="投稿元と動画IDを組み合わせた識別子")
    platform: Literal["youtube", "niconico"]
    title: str = Field(min_length=1)
    thumbnail_url: str = Field(min_length=1, description="画像URLまたはAPI基準のパス")
    video_url: HttpUrl | None = Field(description="元動画のURL。仮データはnull")


    @model_validator(mode="after")
    def validate_source_link(self):
        """投稿元・ID・リンク先を照合し、取り違えた動画データを起動時に検知する。"""
        # 未選定の仮データは引き続き使える。実動画だけを厳密に照合する。
        if self.video_url is None:
            return self
        url = urlsplit(str(self.video_url))
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            raise ValueError("動画URLには認証情報のないHTTPS URLを指定してください")
        if self.platform == "youtube":
            ids = parse_qs(url.query).get("v", [])
            valid = url.hostname == "www.youtube.com" and url.path == "/watch" and len(ids) == 1
            video_id = ids[0] if valid else ""
        else:
            valid = url.hostname == "www.nicovideo.jp" and url.path.startswith("/watch/")
            video_id = url.path.removeprefix("/watch/") if valid else ""
            valid = valid and "/" not in video_id
        if not valid or not video_id or self.id != f"{self.platform}:{video_id}":
            raise ValueError("投稿元・動画ID・動画URLが一致していません。仕様書の正規URL形式を使ってください")
        return self


class VideoList(BaseModel):
    """動画一覧。配列の順番が、そのまま画面の表示順になる。"""
    model_config = ConfigDict(extra="forbid")
    videos: list[Video]


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
def create_app(data_path: Path | None = None) -> FastAPI:
    """APIを作る。テスト時には別のデータファイルを指定できる。"""
    path = data_path if data_path is not None else BACKEND_DIR / "data" / "videos.json"

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """起動時に一度読み込む。壊れたデータを正常な空一覧として扱わない。"""
        application.state.catalog = load_catalog(path)
        yield

    application = FastAPI(title="Video Cross Search API", version="0.1.0", lifespan=lifespan)

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

    @application.get("/health")
    def health() -> dict[str, str]:
        """サーバーが起動できているかを確認するための窓口。"""
        return {"status": "ok"}

    # ── 画面の配信 ─────────────────────────────────────
    # APIと同じサーバーで配信するので、追加の開発サーバーは不要。
    application.mount("/ui", StaticFiles(directory=FRONTEND_DIR), name="ui")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """一覧画面のHTMLを返す。動画自体はブラウザからAPIへ別途要求する。"""
        return FileResponse(FRONTEND_DIR / "index.html")

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        """ブラウザが自動要求するアイコンにも応答して404を防ぐ。"""
        return FileResponse(FRONTEND_DIR / "favicon.svg", media_type="image/svg+xml")

    return application


# Uvicornが「app.main:app」という指定で読み込む入口。
app = create_app()
