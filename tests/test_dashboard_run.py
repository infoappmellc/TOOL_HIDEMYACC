import asyncio
import csv
import io
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from apps.dashboard import main
from apps.dashboard.db import Database, iso, utcnow


def source(url):
    return main.SourceRequest(source_url=url, reel_description="Manual Reel caption",
                              video_content="Manual video body")


def setup_run(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "run_control", main.RunControl())
    db = Database(str(tmp_path / "run.db"))
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "101", "name": "First", "url": "https://facebook.com/101"},
        {"facebook_id": "102", "name": "Second", "url": "https://facebook.com/102"},
    ])
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main.bridge, "connect_profile", Mock(return_value={"connected": True}))
    monkeypatch.setattr(main.bridge, "status", Mock(return_value={
        "connected": True, "profile_name": "FB_01", "cdp_url": "http://127.0.0.1:9000"
    }))
    image = tmp_path / "source.jpg"
    image.write_bytes(b"fixture")
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fixture")
    monkeypatch.setattr(main.extractor, "extract", Mock(return_value={"image_path": str(image)}))
    render = Mock(return_value=video)
    monkeypatch.setattr(main.generator, "render", render)
    monkeypatch.setattr(main.random, "randint", Mock(return_value=17))

    async def fake_publish(**kwargs):
        return {"post_url": f"https://facebook.com/posts/{kwargs['page_id']}"}

    monkeypatch.setattr(main.publisher, "publish", fake_publish)
    return db, render


def test_saved_link_runs_video_and_publish_automatically(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    publish = AsyncMock(return_value={"post_url": "https://facebook.com/posts/101"})
    monkeypatch.setattr(main.publisher, "publish", publish)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/article")))

    result = asyncio.run(main.run_page(first["id"]))

    assert result["duration"] == 17
    assert render.call_args.args[2] == 17
    assert render.call_args.args[1] == "Manual video body"
    assert render.call_args.args[3] == "en"
    assert publish.await_args.kwargs["title"] == "Manual Reel caption"
    page = db.get_page_target(first["id"])
    assert page["workflow_status"] == "published"
    assert all(page[key] == "" for key in
               ("source_url", "reel_description", "video_content", "article_title", "article_language"))
    assert page["post_url"].endswith("/101")
    history = main.page_history(first["id"])["items"]
    assert len(history) == 1
    assert history[0]["post_status"] == history[0]["comment_status"] == "published"
    assert history[0]["article_title"] == "Manual Reel caption"
    assert history[0]["video_content"] == "Manual video body"
    assert history[0]["source_url"] == "https://example.com/article"
    assert [event["step"] for event in main.run_control.events] == [
        "start", "connect", "connected", "page", "extract", "extract_done",
        "render", "render_done", "identity", "inputs_cleared", "page_done", "finish",
    ]


def test_video_content_accepts_and_preserves_long_manual_text(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    long_text = ("To jest długi tekst do filmu.\n" * 150) + "Koniec tekstu."
    payload = main.SourceRequest(source_url="https://example.com/long",
                                 reel_description="Opis filmu", video_content=long_text)

    asyncio.run(main.save_page_source(first["id"], payload))

    assert db.get_page_target(first["id"])["video_content"] == long_text.strip()


def bulk_tsv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t")
    writer.writerow(["page_id", "link_bao", "mo_ta_reel", "noi_dung_video"])
    writer.writerows(rows)
    return output.getvalue()


def test_bulk_import_previews_and_saves_two_pages_without_posting(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    text = bulk_tsv([
        ["101", "https://example.com/one", "Mô tả có dấu, dấu phẩy", "Dòng 1\nDòng 2 rất dài"],
        ["102", "https://example.com/two", "Zweite Beschreibung", "Video body two"],
    ])
    with TestClient(main.app) as client:
        preview = client.post('/api/profiles/1/pages/import', json={"text": text}).json()
        assert preview["errors"] == []
        assert preview["changed"] == 2
        assert preview["overwrite"] == 0
        assert db.get_page_target(db.list_page_targets(1)[0]["id"])["source_url"] == ""
        applied = client.post('/api/profiles/1/pages/import', json={"text": text, "apply": True})
        assert applied.status_code == 200
        assert applied.json() == {"saved": 2, "overwrite": 0}
    pages = {page["facebook_id"]: page for page in db.list_page_targets(1)}
    assert pages["101"]["video_content"] == "Dòng 1\nDòng 2 rất dài"
    assert pages["102"]["reel_description"] == "Zweite Beschreibung"
    assert render.call_count == 0


def test_bulk_import_error_leaves_every_page_unchanged(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    text = bulk_tsv([
        ["101", "https://example.com/one", "Valid caption", "Valid video"],
        ["999", "https://example.com/two", "Wrong Page", "Wrong video"],
    ])
    with TestClient(main.app) as client:
        preview = client.post('/api/profiles/1/pages/import', json={"text": text}).json()
        assert len(preview["errors"]) == 1
        assert "999" in preview["errors"][0]["message"]
        applied = client.post('/api/profiles/1/pages/import', json={"text": text, "apply": True})
        assert applied.status_code == 400
    assert all(not page["source_url"] for page in db.list_page_targets(1))


def test_bulk_import_skips_empty_template_rows_and_flags_overwrite(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/old")))
    text = bulk_tsv([
        ["101", "https://example.com/new", "New caption", "New video"],
        ["102", "", "", ""],
    ])
    preview = main.parse_bulk_page_sources(text, db.list_page_targets(1))
    assert preview["errors"] == []
    assert preview["changed"] == preview["overwrite"] == 1
    assert len(preview["rows"]) == 1
    assert db.get_page_target(second["id"])["source_url"] == ""


def test_bulk_import_refuses_page_with_unfinished_comment(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/old")))
    history = db.create_page_run_history(first["id"], "https://example.com/old")
    db.update_page_run_history(history["id"], post_status="published", comment_status="failed")
    text = bulk_tsv([["101", "https://example.com/new", "New caption", "New video"]])
    preview = main.parse_bulk_page_sources(text, db.list_page_targets(1))
    assert "chạy lại comment" in preview["errors"][0]["message"]
    assert db.get_page_target(first["id"])["source_url"] == "https://example.com/old"


def test_page_with_only_link_cannot_run(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    db.update_page_workflow(first["id"], source_url="https://example.com/article")

    with pytest.raises(main.HTTPException, match="mô tả thước phim và nội dung video"):
        asyncio.run(main.run_page(first["id"]))


def test_changing_manual_content_resets_prepared_post(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/article")))
    db.update_page_workflow(first["id"], post_url="https://facebook.com/reel/123",
                            video_path="/tmp/old-reel.mp4", workflow_status="published")

    changed = main.SourceRequest(source_url="https://example.com/article",
                                 reel_description="New manual caption", video_content="Manual video body")
    page = asyncio.run(main.save_page_source(first["id"], changed))["page"]

    assert page["reel_description"] == page["article_title"] == "New manual caption"
    assert page["video_path"] == page["post_url"] == ""
    assert page["workflow_status"] == "idle"


def test_profile_run_uses_only_pages_with_saved_links(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/one")))

    result = asyncio.run(main.run_profile_pages(1))

    assert result["total"] == result["published"] == 1
    assert result["results"][0]["page_id"] == first["id"]
    assert db.get_page_target(second["id"])["workflow_status"] == "idle"
    assert render.call_count == 1


def test_due_schedule_runs_saved_pages_once(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    for page in (first, second):
        asyncio.run(main.save_page_source(
            page["id"], source(f"https://example.com/{page['facebook_id']}")
        ))
    schedule = db.create_page_schedule(1, [first["id"], second["id"]],
                                       iso(utcnow() - timedelta(seconds=1)))

    asyncio.run(main._run_due_page_schedule_once())

    result = db.list_page_schedules(1)[0]
    assert result["id"] == schedule["id"]
    assert result["status"] == "completed"
    assert [item["status"] for item in result["results"]] == ["published", "published"]
    assert render.call_count == 2
    assert db.get_page_target(first["id"])["source_url"] == ""
    assert db.get_page_target(second["id"])["source_url"] == ""
    assert asyncio.run(main._run_due_page_schedule_once()) is None
    assert render.call_count == 2


def test_due_schedule_skips_page_without_saved_content(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    for page in (first, second):
        asyncio.run(main.save_page_source(
            page["id"], source(f"https://example.com/{page['facebook_id']}")
        ))
    db.create_page_schedule(1, [first["id"], second["id"]], iso(utcnow() - timedelta(seconds=1)))
    db.update_page_workflow(second["id"], video_content="")

    asyncio.run(main._run_due_page_schedule_once())

    result = db.list_page_schedules(1)[0]
    assert [item["status"] for item in result["results"]] == ["skipped", "published"]
    assert render.call_count == 1
    assert db.get_page_target(second["id"])["source_url"] != ""


def test_pending_schedule_blocks_manual_publish_and_waits_for_busy_browser(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/one")))
    db.create_page_schedule(1, [first["id"]], iso(utcnow() - timedelta(seconds=1)))

    with pytest.raises(main.HTTPException, match="huỷ lịch"):
        asyncio.run(main.run_page(first["id"]))
    with pytest.raises(main.HTTPException, match="huỷ lịch"):
        asyncio.run(main.run_profile_pages(1))

    async def while_busy():
        async with main.publish_lock:
            return await main._run_due_page_schedule_once()

    assert asyncio.run(while_busy()) is None
    assert db.active_page_schedule(1)["status"] == "pending"
    assert render.call_count == 0


def test_already_submitted_post_cannot_be_scheduled_or_reposted(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/one")))
    history = db.create_page_run_history(first["id"], "https://example.com/one", "Manual Reel caption")
    db.update_page_run_history(history["id"], post_status="published", comment_status="failed")

    assert db.get_page_target(first["id"])["needs_comment_retry"] == 1
    with pytest.raises(main.HTTPException, match="chạy lại comment"):
        asyncio.run(main.run_page(first["id"]))
    with pytest.raises(main.HTTPException, match="lưu đủ nội dung"):
        asyncio.run(main.run_profile_pages(1))
    with pytest.raises(ValueError, match="comment chưa xong"):
        db.create_page_schedule(1, [first["id"]], iso(utcnow() + timedelta(hours=1)))
    assert render.call_count == 0


def test_schedule_api_creates_and_cancels_future_batch(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/one")))
    when = iso(utcnow() + timedelta(minutes=15))

    with TestClient(main.app) as client:
        created = client.post('/api/profiles/1/pages/schedules', json={"scheduled_at": when})
        assert created.status_code == 200
        schedule = created.json()
        assert schedule["page_ids"] == [first["id"]]
        assert schedule["status"] == "pending"
        assert client.post('/api/profiles/1/pages/schedules', json={"scheduled_at": when}).status_code == 409
        assert client.get('/api/profiles/1/pages/schedules').json()[0]["id"] == schedule["id"]
        assert client.delete(f'/api/profiles/1/pages/schedules/{schedule["id"]}').json() == {"cancelled": True}
        assert client.get('/api/profiles/1/pages/schedules').json()[0]["status"] == "cancelled"


def test_changed_link_clears_old_video_and_post(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    db.update_page_workflow(first["id"], source_url="https://example.com/old",
                            video_path="/tmp/old.mp4", post_url="https://facebook.com/old",
                            workflow_status="published")

    saved = asyncio.run(main.save_page_source(first["id"], source("https://example.com/new")))

    assert saved["page"]["source_url"] == "https://example.com/new"
    assert saved["page"]["video_path"] == saved["page"]["post_url"] == ""
    assert saved["page"]["workflow_status"] == "idle"


def test_profile_run_reports_each_page_when_one_fails(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    for page in (first, second):
        asyncio.run(main.save_page_source(
            page["id"], source(f"https://example.com/{page['facebook_id']}")
        ))

    async def fake_publish(**kwargs):
        if kwargs["page_id"] == "102":
            raise RuntimeError("Facebook rejected this Page")
        return {"post_url": "https://facebook.com/posts/101"}

    monkeypatch.setattr(main.publisher, "publish", fake_publish)

    result = asyncio.run(main.run_profile_pages(1))

    assert result["total"] == 2
    assert result["published"] == 1
    assert [item["status"] for item in result["results"]] == ["published", "failed"]
    assert render.call_count == 2
    assert db.get_page_target(second["id"])["workflow_status"] == "failed"
    failed_history = main.page_history(second["id"])["items"][0]
    assert failed_history["post_status"] == "failed"
    assert failed_history["comment_status"] == "not_started"
    errors = [event for event in main.run_control.events if event["level"] == "error"]
    assert len(errors) == 1
    assert errors[0]["page_id"] == second["id"]
    assert "Facebook rejected this Page" in errors[0]["detail"]


def test_history_separates_post_and_comment_failure(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    video, _ = generated_video(tmp_path, monkeypatch, render)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/one")))

    async def fake_publish(**kwargs):
        kwargs["progress"]("post_wait", "Đã bấm đăng", "")
        kwargs["progress"]("post_found", "Đã tìm thấy bài", "https://facebook.com/reel/123")
        raise RuntimeError("Không thể gửi bình luận")

    monkeypatch.setattr(main.publisher, "publish", fake_publish)
    try:
        asyncio.run(main.run_page(first["id"]))
        assert False, "expected publish failure"
    except Exception as exc:
        assert "Không thể gửi bình luận" in str(exc)
    history = main.page_history(first["id"])["items"][0]
    assert history["post_status"] == "published"
    assert history["comment_status"] == "failed"
    assert history["post_url"] == "https://facebook.com/reel/123"
    assert db.get_page_target(first["id"])["post_url"] == "https://facebook.com/reel/123"
    assert not video.exists()


def generated_video(tmp_path, monkeypatch, render):
    output_dir = tmp_path / "output" / "pages"
    job_dir = output_dir / "job-123"
    job_dir.mkdir(parents=True)
    video = job_dir / "reel.mp4"
    video.write_bytes(b"generated video")
    cover = job_dir / "cover.png"
    cover.write_bytes(b"cover")
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    render.return_value = video
    return video, cover


def test_verified_post_removes_only_generated_video(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    video, cover = generated_video(tmp_path, monkeypatch, render)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/article")))

    asyncio.run(main.run_page(first["id"]))

    assert not video.exists()
    assert cover.exists()
    assert db.get_page_target(first["id"])["video_path"] == ""
    assert main.page_history(first["id"])["items"][0]["video_path"] == ""
    assert any(event["step"] == "cleanup" for event in main.run_control.events)


def test_unverified_post_keeps_video(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    video, _ = generated_video(tmp_path, monkeypatch, render)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/article")))

    async def fake_publish(**kwargs):
        kwargs["progress"]("post_wait", "Đã bấm đăng", "")
        raise RuntimeError("Chưa tìm thấy bài mới")

    monkeypatch.setattr(main.publisher, "publish", fake_publish)
    try:
        asyncio.run(main.run_page(first["id"]))
        assert False, "expected publish failure"
    except Exception as exc:
        assert "Chưa tìm thấy bài mới" in str(exc)

    assert video.exists()
    assert db.get_page_target(first["id"])["video_path"] == str(video)
    assert main.page_history(first["id"])["items"][0]["post_status"] == "submitted"


def test_confirmed_post_keeps_external_video(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    asyncio.run(main.save_page_source(first["id"], source("https://example.com/article")))

    asyncio.run(main.run_page(first["id"]))

    assert (tmp_path / "reel.mp4").exists()
    assert db.get_page_target(first["id"])["video_path"] == str(tmp_path / "reel.mp4")


def test_retry_comment_finds_existing_post_without_reupload(tmp_path, monkeypatch):
    db, _ = setup_run(tmp_path, monkeypatch)
    first = db.list_page_targets(1)[0]
    db.update_page_workflow(first["id"], source_url="https://example.com/article",
                            article_title="Article headline", workflow_status="failed")
    history = db.create_page_run_history(first["id"], "https://example.com/article", "Article headline", "pl")
    db.update_page_run_history(history["id"], post_status="submitted", comment_status="failed")

    async def comment_existing(**kwargs):
        kwargs["progress"]("post_found", "Tìm thấy bài", "https://facebook.com/reel/123")
        kwargs["progress"]("comment_done", "Đã bình luận", "https://facebook.com/reel/123")
        return "https://facebook.com/reel/123"

    comment = AsyncMock(side_effect=comment_existing)
    publish = AsyncMock()
    monkeypatch.setattr(main.publisher, "comment_existing", comment)
    monkeypatch.setattr(main.publisher, "publish", publish)

    result = asyncio.run(main.retry_page_comment(first["id"], history["id"]))

    publish.assert_not_awaited()
    comment.assert_awaited_once()
    assert comment.await_args.kwargs["article_language"] == "pl"
    assert result["history"]["post_status"] == "published"
    assert result["history"]["comment_status"] == "published"
    assert db.get_page_target(first["id"])["post_url"] == "https://facebook.com/reel/123"
    assert db.get_page_target(first["id"])["source_url"] == ""
    assert result["history"]["source_url"] == "https://example.com/article"


def test_pause_waits_before_next_page_and_resume_continues(tmp_path, monkeypatch):
    db, render = setup_run(tmp_path, monkeypatch)
    first, second = db.list_page_targets(1)
    for page in (first, second):
        asyncio.run(main.save_page_source(
            page["id"], source(f"https://example.com/{page['facebook_id']}")
        ))

    async def scenario():
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def fake_publish(**kwargs):
            if kwargs["page_id"] == first["facebook_id"]:
                first_started.set()
                await release_first.wait()
            return {"post_url": f"https://facebook.com/posts/{kwargs['page_id']}"}

        monkeypatch.setattr(main.publisher, "publish", fake_publish)
        task = asyncio.create_task(main.run_profile_pages(1))
        await first_started.wait()
        assert (await main.pause_run())["paused"] is True
        release_first.set()
        await asyncio.sleep(0.1)
        assert render.call_count == 1
        assert not task.done()
        assert (await main.resume_run())["paused"] is False
        result = await task
        assert result["published"] == 2
        assert render.call_count == 2
        assert main.run_control.status()["active"] is False

    asyncio.run(scenario())


def test_progress_stream_sends_current_snapshot(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(main, "run_control", main.RunControl())

    async def scenario():
        main.run_control.start(1)
        request = Mock(is_disconnected=AsyncMock(return_value=True))
        response = await main.run_events(request)
        chunks = [chunk async for chunk in response.body_iterator]
        assert len(chunks) == 1
        assert '"Bắt đầu tác vụ"' in chunks[0]
        assert not main.run_control.subscribers

    asyncio.run(scenario())
