from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .db import Database
from .publisher import BrowserBridge, FacebookPublisher
from .workflow import ArticleExtractor, ReelGenerator


BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parents[1]
app = FastAPI(title="Facebook Profile Pages", version="0.3.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/output", StaticFiles(directory=ROOT_DIR / "output"), name="output")
db = Database()
extractor = ArticleExtractor()
generator = ReelGenerator()
bridge = BrowserBridge()
publisher = FacebookPublisher(bridge)
publish_lock = asyncio.Lock()


class PrepareRequest(BaseModel):
    source_url: str = Field(min_length=10, max_length=2000)
    duration: int = Field(default=20, ge=15, le=30)

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Link phải bắt đầu bằng http:// hoặc https://")
        return value.strip()


class PublishRequest(BaseModel):
    dry_run: bool = True


def default_profile_count() -> int:
    try:
        return min(max(int(os.getenv("DEFAULT_PROFILE_COUNT", "10")), 1), 100)
    except ValueError:
        return 10


db.ensure_default_profiles(default_profile_count())
db.ensure_page_targets(
    "FB_03",
    [
        {
            "facebook_id": "61552971641563",
            "name": "Świat Telenowel | York NY",
            "url": "https://www.facebook.com/profile.php?id=61552971641563",
        },
        {
            "facebook_id": "61553275767963",
            "name": "Rome Lennell Ahuja",
            "url": "https://www.facebook.com/profile.php?id=61553275767963",
        },
    ],
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "dashboard", "version": app.version}


@app.get("/api/profiles")
def profiles() -> list[dict]:
    return db.list_profiles()


@app.get("/api/profiles/{profile_id}/pages")
def profile_pages(profile_id: int) -> list[dict]:
    if not any(profile["id"] == profile_id for profile in db.list_profiles()):
        raise HTTPException(404, "Không tìm thấy profile")
    return db.list_page_targets(profile_id)


@app.post("/api/pages/{page_id}/prepare")
def prepare_page(page_id: int, payload: PrepareRequest) -> dict:
    target = db.get_page_target(page_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy Page")
    db.update_page_workflow(
        page_id, source_url=payload.source_url, workflow_status="preparing", last_error=""
    )
    try:
        work_dir = Path("output/pages/sources") / str(page_id)
        article = extractor.extract(payload.source_url, work_dir)
        video = generator.render(Path(article["image_path"]), article["hook"], payload.duration)
        updated = db.update_page_workflow(
            page_id,
            source_url=payload.source_url,
            video_path=str(video),
            workflow_status="ready",
            last_error="",
        )
        return {"page": updated, "hook": article["hook"], "video_path": str(video)}
    except Exception as exc:
        db.update_page_workflow(page_id, workflow_status="failed", last_error=str(exc))
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/browser/status")
def browser_status() -> dict:
    return bridge.status()


@app.post("/api/profiles/{profile_id}/connect")
def browser_connect(profile_id: int) -> dict:
    profile = next((item for item in db.list_profiles() if item["id"] == profile_id), None)
    if not profile:
        raise HTTPException(404, "Không tìm thấy profile")
    try:
        return bridge.connect_profile(profile["name"])
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/pages/{page_id}/publish")
async def publish_page(page_id: int, payload: PublishRequest) -> dict:
    target = db.get_page_target(page_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy Page")
    video_path = Path(target["video_path"])
    if not target["source_url"] or not video_path.is_file():
        raise HTTPException(400, "Hãy tạo video trước khi đăng")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý một Reel; hãy chờ hoàn tất")
    async with publish_lock:
        return await _publish_page(page_id, target, video_path, payload)


async def _publish_page(page_id: int, target: dict, video_path: Path, payload: PublishRequest) -> dict:
    connection = await asyncio.to_thread(bridge.status)
    if not connection.get("connected") or connection.get("profile_name") != target["profile_name"]:
        raise HTTPException(400, f"Hãy mở và kết nối đúng profile {target['profile_name']} trước khi đăng")
    db.update_page_workflow(page_id, workflow_status="publishing", last_error="")
    try:
        result = await publisher.publish(
            page_id=target["facebook_id"],
            page_name=target["name"],
            video_path=video_path,
            source_url=target["source_url"],
            dry_run=payload.dry_run,
        )
        status = "previewed" if payload.dry_run else "published"
        updated = db.update_page_workflow(
            page_id, workflow_status=status, post_url=result.get("post_url", ""), last_error=""
        )
        return {"page": updated, **result}
    except Exception as exc:
        db.update_page_workflow(page_id, workflow_status="failed", last_error=str(exc))
        raise HTTPException(400, str(exc)) from exc
