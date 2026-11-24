"""固定一覧と検索結果で共用する動画データ形式。"""
from typing import Literal
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
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
    published_at: datetime | None = Field(default=None, description="公開日時。未取得はnull")
    view_count: int | None = Field(default=None, ge=0, description="取得時点の再生数。未取得はnull")
    author_name: str | None = Field(default=None, description="投稿者またはチャンネルの表示名")
    author_id: str | None = Field(default=None, description="名前を取得できない場合に使う投稿者ID")
    metadata_fetched_at: datetime | None = Field(default=None, description="追加情報を取得した日時")


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


