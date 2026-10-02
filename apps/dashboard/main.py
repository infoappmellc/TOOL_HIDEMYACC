from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import random
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

from .db import Database
from .language import article_language
from .publisher import BrowserBridge, FacebookPublisher
from .workflow import OUTPUT_DIR, ArticleExtractor, ReelGenerator


BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parents[1]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.recover_interrupted_page_schedules()
    db.expire_missed_page_schedules()
    scheduler = asyncio.create_task(_schedule_loop())
    try:
        yield
    finally:
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler


app = FastAPI(title="Facebook Profile Pages", version="0.5.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/output", StaticFiles(directory=ROOT_DIR / "output"), name="output")
db = Database()
extractor = ArticleExtractor()
generator = ReelGenerator()
bridge = BrowserBridge()
publisher = FacebookPublisher(bridge)
publish_lock = asyncio.Lock()


class RunControl:
    def __init__(self) -> None:
        self.active = False
        self.paused = False
        self.waiting = False
        self.profile_id: int | None = None
        self.page_id: int | None = None
        self.ready = asyncio.Event()
        self.ready.set()
        self.events: list[dict] = []
        self.sequence = 0
        self.subscribers: set[asyncio.Queue] = set()

    def emit(self, step: str, message: str, level: str = "info", detail: str = "") -> None:
        self.sequence += 1
        event = {"id": self.sequence, "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "page_id": self.page_id, "step": step, "level": level,
                 "message": message, "detail": detail[:6000]}
        self.events.append(event)
        self.events = self.events[-150:]
        self._broadcast()

    def start(self, profile_id: int) -> None:
        self.events = []
        self.active = True
        self.paused = False
        self.waiting = False
        self.profile_id = profile_id
        self.page_id = None
        self.ready.set()
        self.emit("start", "Bắt đầu tác vụ")

    def finish(self) -> None:
        self.emit("finish", "Tác vụ đã kết thúc")
        self.active = False
        self.paused = False
        self.waiting = False
        self.profile_id = None
        self.page_id = None
        self.ready.set()
        self._broadcast()

    def _broadcast(self) -> None:
        snapshot = self.status()
        for subscriber in tuple(self.subscribers):
            if subscriber.full():
                subscriber.get_nowait()
            subscriber.put_nowait(snapshot)

    async def checkpoint(self) -> None:
        self.waiting = not self.ready.is_set()
        try:
            await self.ready.wait()
        finally:
            self.waiting = False

    def status(self) -> dict:
        return {"active": self.active, "paused": self.paused, "waiting": self.waiting,
                "profile_id": self.profile_id, "page_id": self.page_id, "events": list(self.events)}


run_control = RunControl()


def error_detail(exc: Exception) -> str:
    details = []
    current: BaseException | None = exc
    while current and len(details) < 4:
        message = current.detail if isinstance(current, HTTPException) else str(current)
        details.append(f"{type(current).__name__}: {message or 'không có thông báo'}")
        current = current.__cause__
    return "\nGây ra bởi: ".join(details)


class SourceRequest(BaseModel):
    source_url: str = Field(min_length=10, max_length=2000)
    reel_description: str = Field(min_length=1, max_length=2000)
    video_content: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Link phải bắt đầu bằng http:// hoặc https://")
        return value.strip()

    @field_validator("reel_description", "video_content")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nội dung nhập thủ công không được để trống")
        return value


class PublishRequest(BaseModel):
    dry_run: bool = True


class BulkSourceRequest(BaseModel):
    text: str = Field(min_length=1)
    apply: bool = False


class PageScheduleRequest(BaseModel):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def future_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Giờ hẹn cần kèm múi giờ")
        value = value.astimezone(timezone.utc)
        if value < datetime.now(timezone.utc) + timedelta(seconds=10):
            raise ValueError("Hãy chọn thời gian cách hiện tại ít nhất 10 giây")
        return value


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


@app.get("/api/pages/{page_id}/history")
def page_history(page_id: int, limit: int = 50, offset: int = 0) -> dict:
    if not db.get_page_target(page_id):
        raise HTTPException(404, "Không tìm thấy Page")
    return db.list_page_run_history(page_id, limit, offset)


@app.post("/api/profiles/{profile_id}/pages/refresh")
async def refresh_profile_pages(profile_id: int) -> dict:
    profile = next((item for item in db.list_profiles() if item["id"] == profile_id), None)
    if not profile:
        raise HTTPException(404, "Không tìm thấy profile")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        try:
            connection = await asyncio.to_thread(bridge.connect_profile, profile["name"])
            targets = await publisher.scan_pages(profile["name"])
            pages = db.sync_page_targets(profile_id, targets)
            return {"count": len(pages), "pages": pages, "connection": connection}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/pages/{page_id}/source")
async def save_page_source(page_id: int, payload: SourceRequest) -> dict:
    target = db.get_page_target(page_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy Page")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        if all(target[key] == getattr(payload, key) for key in
               ("source_url", "reel_description", "video_content")):
            return {"page": target}
        language = article_language(f"{payload.reel_description} {payload.video_content}")
        updated = db.update_page_workflow(
            page_id, source_url=payload.source_url, reel_description=payload.reel_description,
            video_content=payload.video_content, article_title=payload.reel_description,
            article_language=language, video_path="", post_url="",
            workflow_status="idle", last_error="",
        )
        return {"page": updated}


def parse_bulk_page_sources(text: str, pages: list[dict]) -> dict:
    """Parse tab-separated spreadsheet rows; quoted cells may contain newlines."""
    by_facebook_id = {str(page["facebook_id"]): page for page in pages}
    errors: list[dict] = []
    rows: list[dict] = []
    seen: set[str] = set()
    reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t", strict=True)
    try:
        header = next(reader, [])
        if [cell.lstrip("\ufeff").strip().lower() for cell in header] != [
            "page_id", "link_bao", "mo_ta_reel", "noi_dung_video"
        ]:
            return {"rows": [], "errors": [{"line": 1, "message":
                    "Dòng đầu phải là: page_id [Tab] link_bao [Tab] mo_ta_reel [Tab] noi_dung_video"}],
                    "changed": 0, "overwrite": 0}
        for values in reader:
            line = reader.line_num
            if not any(value.strip() for value in values):
                continue
            if len(values) != 4:
                errors.append({"line": line, "message": f"Cần 4 cột, hiện có {len(values)} cột"})
                continue
            facebook_id, url, description, content = (value.strip() for value in values)
            if not any((url, description, content)):
                continue  # Empty template row: no update for this Page.
            if facebook_id in seen:
                errors.append({"line": line, "message": f"Page ID {facebook_id} bị lặp"})
                continue
            seen.add(facebook_id)
            page = by_facebook_id.get(facebook_id)
            if not page:
                errors.append({"line": line, "message": f"Page ID {facebook_id or '(trống)'} không thuộc profile đang chọn"})
                continue
            if page["needs_comment_retry"]:
                errors.append({"line": line, "message": f"{page['name']} cần chạy lại comment trong lịch sử; không ghi đè"})
                continue
            try:
                source = SourceRequest(source_url=url, reel_description=description, video_content=content)
            except ValidationError as exc:
                issue = exc.errors()[0]
                field = {"source_url": "Link báo", "reel_description": "Mô tả Reel",
                         "video_content": "Nội dung video"}.get(issue["loc"][0], "Nội dung")
                errors.append({"line": line, "message": f"{page['name']}: {field} — {issue['msg']}"})
                continue
            fields = source.model_dump()
            changed = any(page[key] != fields[key] for key in fields)
            overwrite = changed and any(page[key] for key in fields)
            rows.append({"id": page["id"], "facebook_id": facebook_id, "name": page["name"],
                         **fields, "article_language": article_language(
                             f"{source.reel_description} {source.video_content}"
                         ), "action": "overwrite" if overwrite else "new" if changed else "unchanged"})
    except csv.Error as exc:
        errors.append({"line": reader.line_num, "message": f"Dữ liệu Tab không hợp lệ: {exc}"})
    return {"rows": rows, "errors": errors,
            "changed": sum(row["action"] != "unchanged" for row in rows),
            "overwrite": sum(row["action"] == "overwrite" for row in rows)}


@app.post("/api/profiles/{profile_id}/pages/import")
async def import_page_sources(profile_id: int, payload: BulkSourceRequest) -> dict:
    if not any(profile["id"] == profile_id for profile in db.list_profiles()):
        raise HTTPException(404, "Không tìm thấy profile")
    preview = parse_bulk_page_sources(payload.text, db.list_page_targets(profile_id))
    if not payload.apply:
        return preview
    if preview["errors"]:
        first = preview["errors"][0]
        raise HTTPException(400, f"Dòng {first['line']}: {first['message']}; chưa lưu Page nào")
    if not preview["changed"]:
        raise HTTPException(400, "Không có nội dung mới để lưu")
    if publish_lock.locked():
        raise HTTPException(409, "Đang đăng bài; hãy nhập sau khi tác vụ kết thúc")
    async with publish_lock:
        try:
            changed_rows = [row for row in preview["rows"] if row["action"] != "unchanged"]
            count = db.bulk_save_page_sources(profile_id, changed_rows)
            return {"saved": count, "overwrite": preview["overwrite"]}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


async def _run_page(target: dict) -> dict:
    page_id = target["id"]
    if target.get("needs_comment_retry"):
        raise HTTPException(409, f"Page {target['name']} đã gửi bài; hãy chạy lại comment trong lịch sử, không đăng Reel mới")
    run_control.page_id = page_id
    await run_control.checkpoint()
    run_control.emit("page", f"Bắt đầu Page {target['name']}")
    if not all(target[key] for key in ("source_url", "reel_description", "video_content")):
        raise HTTPException(400, f"Page {target['name']} chưa lưu đủ link, mô tả thước phim và nội dung video")
    history_id = db.create_page_run_history(
        page_id, target["source_url"], target["reel_description"], target["article_language"],
        target["video_content"],
    )["id"]
    db.update_page_workflow(page_id, workflow_status="preparing", last_error="")
    stage = "extract"
    try:
        run_control.emit("extract", "Đang lấy ảnh từ link bài báo")
        work_dir = Path("output/pages/sources") / str(page_id)
        article = await asyncio.to_thread(extractor.extract, target["source_url"], work_dir)
        db.update_page_run_history(history_id, step="extracted")
        run_control.emit("extract_done", "Đã lấy ảnh bài báo; dùng nội dung nhập thủ công", "success")
        stage = "render"
        db.update_page_run_history(history_id, step="rendering")
        duration = generator.display_duration(target["video_content"], random.randint(15, 30))
        run_control.emit("render", "Đang tạo video và ghép nhạc ngẫu nhiên")
        video = await asyncio.to_thread(
            generator.render, Path(article["image_path"]), target["video_content"], duration,
            target["article_language"],
        )
        db.update_page_workflow(page_id, video_path=str(video), workflow_status="ready", last_error="")
        db.update_page_run_history(history_id, video_path=str(video), step="ready")
        run_control.emit("render_done", "Video đã tạo xong", "success")
    except Exception as exc:
        db.update_page_workflow(page_id, workflow_status="failed", last_error=str(exc))
        db.update_page_run_history(history_id, post_status="failed", step="failed", error=error_detail(exc))
        run_control.emit("error", f"Lỗi Page {target['name']} khi {'lấy bài báo' if stage == 'extract' else 'tạo video'}: {exc}",
                         "error", error_detail(exc))
        raise HTTPException(400, str(exc)) from exc
    await run_control.checkpoint()
    result = await _publish_page(
        page_id, db.get_page_target(page_id), video, PublishRequest(dry_run=False), history_id
    )
    run_control.emit("page_done", f"Page {target['name']} đã đăng và bình luận xong", "success")
    return {"page_id": page_id, "duration": duration, **result}


@app.post("/api/pages/{page_id}/run")
async def run_page(page_id: int) -> dict:
    target = db.get_page_target(page_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy Page")
    if target["needs_comment_retry"]:
        raise HTTPException(409, "Page đã gửi bài; hãy chạy lại comment trong lịch sử trước")
    if db.active_page_schedule(target["profile_id"]):
        raise HTTPException(409, "Profile đang có lịch hẹn; hãy huỷ lịch trước khi đăng ngay")
    if not all(target[key] for key in ("source_url", "reel_description", "video_content")):
        raise HTTPException(400, "Hãy lưu link, mô tả thước phim và nội dung video trước khi chạy")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        run_control.start(target["profile_id"])
        try:
            run_control.emit("connect", f"Đang kết nối profile {target['profile_name']}")
            await asyncio.to_thread(bridge.connect_profile, target["profile_name"])
            run_control.emit("connected", "Browser đã kết nối", "success")
            return await _run_page(target)
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            run_control.emit("error", f"Lỗi kết nối/chạy Page: {exc}", "error", error_detail(exc))
            raise HTTPException(400, str(exc)) from exc
        finally:
            run_control.finish()


async def _run_profile_batch(profile: dict, targets: list[dict],
                             initial_results: list[dict] | None = None,
                             schedule_id: int | None = None) -> dict:
    run_control.start(profile["id"])
    results = list(initial_results or [])
    try:
        if schedule_id:
            run_control.emit("schedule", f"Bắt đầu lịch đăng #{schedule_id} cho {profile['name']}")
        if targets:
            run_control.emit("connect", f"Đang kết nối profile {profile['name']}")
            await asyncio.to_thread(bridge.connect_profile, profile["name"])
            run_control.emit("connected", "Browser đã kết nối", "success")
        for target in targets:
            try:
                result = await _run_page(target)
                results.append({"page_id": target["id"], "name": target["name"],
                                "status": "published", "post_url": result.get("post_url", "")})
            except HTTPException as exc:
                results.append({"page_id": target["id"], "name": target["name"],
                                "status": "failed", "error": str(exc.detail)})
        return {"total": len(results), "published": sum(item["status"] == "published" for item in results),
                "results": results}
    except HTTPException:
        raise
    except Exception as exc:
        run_control.emit("error", f"Lỗi kết nối profile: {exc}", "error", error_detail(exc))
        raise HTTPException(400, str(exc)) from exc
    finally:
        run_control.finish()


@app.post("/api/profiles/{profile_id}/pages/run")
async def run_profile_pages(profile_id: int) -> dict:
    profile = next((item for item in db.list_profiles() if item["id"] == profile_id), None)
    if not profile:
        raise HTTPException(404, "Không tìm thấy profile")
    if db.active_page_schedule(profile_id):
        raise HTTPException(409, "Profile đang có lịch hẹn; hãy huỷ lịch trước khi đăng ngay")
    targets = [page for page in db.list_page_targets(profile_id)
               if all(page[key] for key in ("source_url", "reel_description", "video_content"))
               and not page["needs_comment_retry"]]
    if not targets:
        raise HTTPException(400, "Profile chưa có Page nào lưu đủ nội dung")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        return await _run_profile_batch(profile, targets)


@app.get("/api/profiles/{profile_id}/pages/schedules")
def page_schedules(profile_id: int) -> list[dict]:
    if not any(profile["id"] == profile_id for profile in db.list_profiles()):
        raise HTTPException(404, "Không tìm thấy profile")
    return db.list_page_schedules(profile_id)


@app.post("/api/profiles/{profile_id}/pages/schedules")
def create_page_schedule(profile_id: int, payload: PageScheduleRequest) -> dict:
    if publish_lock.locked():
        raise HTTPException(409, "Đang chạy tác vụ khác; hãy lên lịch sau khi hoàn tất")
    if not any(profile["id"] == profile_id for profile in db.list_profiles()):
        raise HTTPException(404, "Không tìm thấy profile")
    targets = [page for page in db.list_page_targets(profile_id)
               if all(page[key] for key in ("source_url", "reel_description", "video_content"))
               and not page["needs_comment_retry"]]
    if not targets:
        raise HTTPException(400, "Profile chưa có Page nào lưu đủ nội dung để lên lịch")
    try:
        return db.create_page_schedule(
            profile_id, [page["id"] for page in targets], payload.scheduled_at.isoformat(timespec="seconds")
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/profiles/{profile_id}/pages/schedules/{schedule_id}")
def cancel_page_schedule(profile_id: int, schedule_id: int) -> dict:
    if not db.cancel_page_schedule(schedule_id, profile_id):
        raise HTTPException(409, "Lịch không còn chờ để huỷ")
    return {"cancelled": True}


async def _run_due_page_schedule_once() -> dict | None:
    if publish_lock.locked():
        return None
    async with publish_lock:
        schedule = db.claim_due_page_schedule()
        if not schedule:
            return None
        skipped = []
        try:
            profile = next((item for item in db.list_profiles() if item["id"] == schedule["profile_id"]), None)
            pages = {page["id"]: page for page in db.list_page_targets(schedule["profile_id"])}
            targets = []
            for page_id in schedule["page_ids"]:
                page = pages.get(page_id)
                if page and not page["needs_comment_retry"] and all(
                    page[key] for key in ("source_url", "reel_description", "video_content")
                ):
                    targets.append(page)
                else:
                    skipped.append({"page_id": page_id, "name": page["name"] if page else f"Page #{page_id}",
                                    "status": "skipped", "error": "Page đã gửi bài và cần xử lý comment trong lịch sử" if page and page["needs_comment_retry"] else "Page không còn đủ nội dung đã lưu"})
            if not profile or not targets:
                db.finish_page_schedule(schedule["id"], "failed", skipped,
                                        "Profile hoặc các Page đã hẹn không còn sẵn sàng; không đăng gì")
                return schedule
            result = await _run_profile_batch(profile, targets, skipped, schedule["id"])
            errors = [item for item in result["results"] if item["status"] != "published"]
            db.finish_page_schedule(schedule["id"], "completed", result["results"],
                                    f"{len(errors)} Page chưa hoàn tất; xem lịch sử từng Page" if errors else "")
        except Exception as exc:
            db.finish_page_schedule(schedule["id"], "failed", skipped, error_detail(exc))
        return schedule


async def _schedule_loop() -> None:
    while True:
        try:
            await _run_due_page_schedule_once()
        except Exception as exc:
            # Keep the local scheduler alive; a claimed schedule is marked as
            # interrupted on restart rather than posted a second time.
            run_control.emit("schedule_error", f"Lỗi bộ hẹn giờ: {exc}", "error", error_detail(exc))
        await asyncio.sleep(2)


@app.get("/api/run/status")
def run_status() -> dict:
    return run_control.status()


@app.get("/api/run/events")
async def run_events(request: Request) -> StreamingResponse:
    async def stream():
        subscriber: asyncio.Queue = asyncio.Queue(maxsize=10)
        run_control.subscribers.add(subscriber)
        try:
            yield f"data: {json.dumps(run_control.status(), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    snapshot = await asyncio.wait_for(subscriber.get(), timeout=15)
                    yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            run_control.subscribers.discard(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/run/pause")
async def pause_run() -> dict:
    if not run_control.active:
        raise HTTPException(409, "Không có tác vụ đang chạy")
    run_control.paused = True
    run_control.ready.clear()
    run_control.emit("pause", "Đã yêu cầu tạm dừng ở điểm an toàn")
    return run_control.status()


@app.post("/api/run/resume")
async def resume_run() -> dict:
    if not run_control.active:
        raise HTTPException(409, "Không có tác vụ đang tạm dừng")
    run_control.paused = False
    run_control.ready.set()
    run_control.emit("resume", "Đã tiếp tục tác vụ", "success")
    return run_control.status()


@app.get("/api/browser/status")
def browser_status(profile_name: str = "") -> dict:
    return bridge.status(profile_name)


@app.post("/api/profiles/{profile_id}/connect")
async def browser_connect(profile_id: int) -> dict:
    profile = next((item for item in db.list_profiles() if item["id"] == profile_id), None)
    if not profile:
        raise HTTPException(404, "Không tìm thấy profile")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        try:
            return await asyncio.to_thread(bridge.connect_profile, profile["name"])
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/pages/{page_id}/publish")
async def publish_page(page_id: int, payload: PublishRequest) -> dict:
    target = db.get_page_target(page_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy Page")
    video_path = Path(target["video_path"])
    if not target["source_url"] or not target["reel_description"] or not video_path.is_file():
        raise HTTPException(400, "Hãy tạo video trước khi đăng")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý một Reel; hãy chờ hoàn tất")
    async with publish_lock:
        history_id = db.create_page_run_history(
            page_id, target["source_url"], target["article_title"], target["article_language"],
            target["video_content"],
        )["id"]
        return await _publish_page(page_id, target, video_path, payload, history_id)


@app.post("/api/pages/{page_id}/history/{history_id}/comment/retry")
async def retry_page_comment(page_id: int, history_id: int) -> dict:
    target = db.get_page_target(page_id)
    history = db.get_page_run_history(history_id)
    if not target or not history or history["page_id"] != page_id:
        raise HTTPException(404, "Không tìm thấy lần đăng của Page")
    if history["post_status"] not in {"submitted", "published"} or history["comment_status"] == "published":
        raise HTTPException(409, "Lần đăng này không cần tìm lại để bình luận")
    if not history["article_title"] or not history["source_url"]:
        raise HTTPException(400, "Lịch sử thiếu tiêu đề hoặc link bài báo")
    if publish_lock.locked():
        raise HTTPException(409, "Browser đang xử lý tác vụ khác; hãy chờ hoàn tất")
    async with publish_lock:
        run_control.start(target["profile_id"])
        run_control.page_id = page_id
        run_control.emit("recover", f"Tìm lại bài của {target['name']} để comment, không đăng video mới")

        def progress(step: str, message: str, post_url: str = "") -> None:
            run_control.emit(step, message)
            updates: dict[str, str] = {"step": step}
            if step == "post_found":
                updates.update(post_status="published", comment_status="pending")
            elif step == "comment":
                updates["comment_status"] = "pending"
            elif step == "comment_done":
                updates.update(post_status="published", comment_status="published", error="")
            if post_url:
                updates["post_url"] = post_url
            db.update_page_run_history(history_id, **updates)

        try:
            await asyncio.to_thread(bridge.connect_profile, target["profile_name"])
            post_url = await publisher.comment_existing(
                profile_name=target["profile_name"], page_id=target["facebook_id"],
                page_name=target["name"], source_url=history["source_url"],
                title=history["article_title"], article_language=history["article_language"], progress=progress,
            )
            current = db.get_page_run_history(history_id)
            if current["comment_status"] != "published":
                db.update_page_run_history(history_id, post_status="published",
                                           comment_status="published", step="completed", error="")
            if target["source_url"] == history["source_url"]:
                db.update_page_workflow(page_id, workflow_status="published",
                                        post_url=post_url or history["post_url"], last_error="")
                if target["video_path"] and target["video_path"] == history["video_path"]:
                    _remove_published_video(page_id, history_id, Path(history["video_path"]))
                if db.clear_published_page_inputs(page_id, history_id):
                    run_control.emit("inputs_cleared", "Đã dọn ô nhập; nội dung vẫn có trong lịch sử", "success")
            run_control.emit("recover_done", "Đã bình luận trên bài hiện có; không đăng Reel mới", "success")
            return {"post_url": post_url or history["post_url"], "history": db.get_page_run_history(history_id)}
        except Exception as exc:
            db.update_page_run_history(history_id, comment_status="failed", step="failed", error=error_detail(exc))
            run_control.emit("error", f"Chưa bình luận được trên bài cũ: {exc}", "error", error_detail(exc))
            raise HTTPException(400, str(exc)) from exc
        finally:
            run_control.finish()


async def _publish_page(
    page_id: int, target: dict, video_path: Path, payload: PublishRequest, history_id: int | None = None
) -> dict:
    history_state = {"post_status": "uploading", "comment_status": "not_started", "post_url": ""}

    def track_progress(step: str, message: str, post_url: str = "") -> None:
        if run_control.active:
            run_control.emit(step, message)
        updates: dict[str, str] = {"step": step}
        if step == "post_wait":
            updates["post_status"] = "submitted"
        elif step == "post_found":
            updates.update(post_status="published", comment_status="pending")
        elif step == "comment":
            updates["comment_status"] = "pending"
        elif step == "comment_done":
            updates.update(post_status="published", comment_status="published")
        if post_url:
            updates["post_url"] = post_url
            history_state["post_url"] = post_url
        history_state.update({key: value for key, value in updates.items()
                              if key in {"post_status", "comment_status"}})
        if history_id is not None:
            db.update_page_run_history(history_id, **updates)

    if run_control.active:
        run_control.emit("identity", f"Đang kiểm tra đúng profile và Page {target['name']}")
    if history_id is not None:
        db.update_page_run_history(history_id, post_status="uploading", step="identity")
    try:
        connection = await asyncio.to_thread(bridge.status, target["profile_name"])
    except Exception as exc:
        if history_id is not None:
            db.update_page_run_history(history_id, post_status="failed", step="failed", error=error_detail(exc))
        if run_control.active:
            run_control.emit("error", f"Lỗi kiểm tra kết nối profile {target['profile_name']}: {exc}",
                             "error", error_detail(exc))
        raise HTTPException(400, str(exc)) from exc
    if not connection.get("connected") or connection.get("profile_name") != target["profile_name"]:
        if history_id is not None:
            db.update_page_run_history(history_id, post_status="failed", step="failed",
                                       error=f"Profile {target['profile_name']} chưa kết nối đúng")
        if run_control.active:
            run_control.emit("error", f"Profile {target['profile_name']} chưa kết nối đúng; dừng trước khi đăng",
                             "error")
        raise HTTPException(400, f"Hãy mở và kết nối đúng profile {target['profile_name']} trước khi đăng")
    db.update_page_workflow(page_id, workflow_status="publishing", last_error="")
    try:
        result = await publisher.publish(
            profile_name=target["profile_name"],
            page_id=target["facebook_id"],
            page_name=target["name"],
            video_path=video_path,
            source_url=target["source_url"],
            title=target["reel_description"],
            article_language=target["article_language"],
            dry_run=payload.dry_run,
            before_post=run_control.checkpoint if run_control.active else None,
            progress=track_progress,
        )
        status = "previewed" if payload.dry_run else "published"
        updated = db.update_page_workflow(
            page_id, workflow_status=status, post_url=result.get("post_url", ""), last_error=""
        )
        if history_id is not None:
            db.update_page_run_history(
                history_id, post_status="previewed" if payload.dry_run else "published",
                comment_status="not_applicable" if payload.dry_run else "published",
                post_url=result.get("post_url", ""), step="previewed" if payload.dry_run else "completed",
            )
        if not payload.dry_run:
            _remove_published_video(page_id, history_id, video_path)
            if history_id is not None and db.clear_published_page_inputs(page_id, history_id):
                if run_control.active:
                    run_control.emit("inputs_cleared", "Đã dọn ô nhập; nội dung vẫn có trong lịch sử", "success")
            updated = db.get_page_target(page_id)
        return {"page": updated, **result}
    except Exception as exc:
        workflow_update = {"workflow_status": "failed", "last_error": str(exc)}
        if history_state["post_status"] == "published" and history_state["post_url"]:
            workflow_update["post_url"] = history_state["post_url"]
        db.update_page_workflow(page_id, **workflow_update)
        if history_id is not None:
            post_status = history_state["post_status"]
            db.update_page_run_history(
                history_id,
                post_status=post_status if post_status in {"submitted", "published"} else "failed",
                comment_status="failed" if post_status in {"submitted", "published"} else "not_started",
                step="failed", error=error_detail(exc),
            )
        if history_state["post_status"] == "published":
            _remove_published_video(page_id, history_id, video_path)
        if run_control.active:
            run_control.emit("error", f"Lỗi đăng/bình luận Page {target['name']}: {exc}",
                             "error", error_detail(exc))
        raise HTTPException(400, str(exc)) from exc


def _remove_published_video(page_id: int, history_id: int | None, video_path: Path) -> None:
    """Delete only a generated Reel after its Facebook post has been verified."""
    path = Path(video_path)
    root = OUTPUT_DIR.resolve()
    resolved = path.resolve(strict=False)
    if path.is_symlink() or resolved.name != "reel.mp4" or resolved.parent.parent != root:
        return
    try:
        path.unlink(missing_ok=True)
        db.update_page_workflow(page_id, video_path="")
        if history_id is not None:
            db.update_page_run_history(history_id, video_path="")
        if run_control.active:
            run_control.emit("cleanup", "Đã xoá video local sau khi xác nhận bài đăng", "success")
    except Exception as exc:
        if run_control.active:
            run_control.emit("cleanup_warning", f"Bài đã đăng nhưng chưa xoá được video: {exc}",
                             "warning", error_detail(exc))
