from datetime import timedelta

from apps.dashboard.db import Database, iso, utcnow


def make_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def add_profile(db):
    return db.create_profile(
        {
            "name": "Trang thử nghiệm",
            "hidemyacc_id": "hma-profile-001",
            "target_url": "https://www.facebook.com/example",
        }
    )


def add_job(db, profile_id, scheduled_at=None):
    return db.create_job(
        {
            "profile_id": profile_id,
            "title": "Video test",
            "caption": "Caption test",
            "scheduled_at": scheduled_at or iso(utcnow() - timedelta(seconds=1)),
        }
    )


def test_profile_and_job_lifecycle(tmp_path):
    db = make_db(tmp_path)
    profile = add_profile(db)
    job = add_job(db, profile["id"])
    agent = db.upsert_agent({"name": "test-agent", "version": "test"})

    claimed = db.claim_job(agent["id"])
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "processing"
    assert db.claim_job(agent["id"]) is None

    assert db.add_event(job["id"], agent["id"], "info", "rendered")
    assert db.finish_job(job["id"], agent["id"], "completed", "/tmp/video.mp4")
    finished = db.get_job(job["id"])
    assert finished["status"] == "completed"
    assert finished["artifact_path"] == "/tmp/video.mp4"
    assert any(event["message"] == "rendered" for event in finished["events"])


def test_future_job_is_not_claimed(tmp_path):
    db = make_db(tmp_path)
    profile = add_profile(db)
    add_job(db, profile["id"], iso(utcnow() + timedelta(hours=1)))
    agent = db.upsert_agent({"name": "test-agent", "version": "test"})
    assert db.claim_job(agent["id"]) is None


def test_cancel_and_retry(tmp_path):
    db = make_db(tmp_path)
    profile = add_profile(db)
    job = add_job(db, profile["id"])
    assert db.cancel_job(job["id"])
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert db.retry_job(job["id"])
    assert db.get_job(job["id"])["status"] == "queued"


def test_cannot_duplicate_hidemyacc_id(tmp_path):
    db = make_db(tmp_path)
    add_profile(db)
    try:
        add_profile(db)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "đã tồn tại" in str(exc)


def test_default_profiles_are_created_without_hidemyacc_api(tmp_path):
    db = make_db(tmp_path)
    assert db.ensure_default_profiles(3) == 3
    assert db.ensure_default_profiles(3) == 0
    profiles = db.list_profiles()
    assert [profile["name"] for profile in profiles] == ["FB_01", "FB_02", "FB_03"]
    assert all(profile["source"] == "default" for profile in profiles)


def test_page_workflow_state_is_saved(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(3)
    db.ensure_page_targets(
        "FB_03",
        [{"facebook_id": "123456789", "name": "Demo Page", "url": "https://facebook.com/123456789"}],
    )
    page = db.list_page_targets()[0]
    updated = db.update_page_workflow(
        page["id"], source_url="https://example.com/article", video_path="/tmp/reel.mp4", workflow_status="ready"
    )
    assert updated["workflow_status"] == "ready"
    assert db.list_page_targets(updated["profile_id"])[0]["source_url"] == "https://example.com/article"


def test_page_history_is_persistent_and_paginated(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "123", "name": "Demo Page", "url": "https://facebook.com/123"},
    ])
    page_id = db.list_page_targets(1)[0]["id"]
    first = db.create_page_run_history(page_id, "https://example.com/one")
    db.update_page_run_history(first["id"], article_title="First", post_status="submitted",
                               comment_status="failed", error="Không tìm thấy bài")
    second = db.create_page_run_history(page_id, "https://example.com/two")
    db.update_page_run_history(second["id"], post_status="published", comment_status="published")

    reopened = Database(str(tmp_path / "test.db"))
    newest = reopened.list_page_run_history(page_id, limit=1)
    older = reopened.list_page_run_history(page_id, limit=1, offset=1)
    assert newest["total"] == 2
    assert newest["items"][0]["id"] == second["id"]
    assert older["items"][0]["article_title"] == "First"
    assert older["items"][0]["comment_status"] == "failed"


def test_completed_page_inputs_are_archived_before_clearing(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "123", "name": "Demo Page", "url": "https://facebook.com/123"},
    ])
    page_id = db.list_page_targets(1)[0]["id"]
    db.update_page_workflow(page_id, source_url="https://example.com/news",
                            reel_description="Manual caption", video_content="Manual video text",
                            article_title="Manual caption", workflow_status="published",
                            post_url="https://facebook.com/reel/123")
    history = db.create_page_run_history(page_id, "https://example.com/news", "Manual caption")
    db.update_page_run_history(history["id"], post_status="published", comment_status="failed")
    assert not db.clear_published_page_inputs(page_id, history["id"])
    assert db.get_page_target(page_id)["source_url"] == "https://example.com/news"

    db.update_page_run_history(history["id"], comment_status="published")
    assert db.clear_published_page_inputs(page_id, history["id"])
    page = db.get_page_target(page_id)
    assert all(page[key] == "" for key in ("source_url", "reel_description", "video_content"))
    assert page["workflow_status"] == "published"
    assert page["post_url"] == "https://facebook.com/reel/123"
    assert db.get_page_run_history(history["id"])["video_content"] == "Manual video text"


def test_page_schedule_is_persistent_claimed_once_and_can_be_cancelled(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "123", "name": "Demo Page", "url": "https://facebook.com/123"},
    ])
    page_id = db.list_page_targets(1)[0]["id"]
    db.update_page_workflow(page_id, source_url="https://example.com/news",
                            reel_description="Reel caption", video_content="Video body")
    future = iso(utcnow() + timedelta(hours=1))
    first = db.create_page_schedule(1, [page_id], future)
    reopened = Database(str(tmp_path / "test.db"))
    assert reopened.list_page_schedules(1)[0]["page_ids"] == [page_id]
    assert reopened.claim_due_page_schedule() is None
    assert reopened.cancel_page_schedule(first["id"], 1)
    assert not reopened.cancel_page_schedule(first["id"], 1)

    due = reopened.create_page_schedule(1, [page_id], iso(utcnow() - timedelta(seconds=1)))
    assert reopened.claim_due_page_schedule()["id"] == due["id"]
    assert reopened.claim_due_page_schedule() is None
    reopened.finish_page_schedule(due["id"], "completed", [{"page_id": page_id, "status": "published"}])
    assert reopened.list_page_schedules(1)[0]["results"][0]["status"] == "published"
    assert reopened.claim_due_page_schedule() is None


def test_running_schedule_is_not_reposted_after_restart(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "123", "name": "Demo Page", "url": "https://facebook.com/123"},
    ])
    page_id = db.list_page_targets(1)[0]["id"]
    db.update_page_workflow(page_id, source_url="https://example.com/news",
                            reel_description="Reel caption", video_content="Video body")
    db.create_page_schedule(1, [page_id], iso(utcnow() - timedelta(seconds=1)))
    db.claim_due_page_schedule()
    reopened = Database(str(tmp_path / "test.db"))
    assert reopened.recover_interrupted_page_schedules() == 1
    assert reopened.claim_due_page_schedule() is None
    assert "kiểm tra lịch sử" in reopened.list_page_schedules(1)[0]["error"]


def test_missed_schedule_is_not_posted_late_after_dashboard_restart(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "123", "name": "Demo Page", "url": "https://facebook.com/123"},
    ])
    page_id = db.list_page_targets(1)[0]["id"]
    db.update_page_workflow(page_id, source_url="https://example.com/news",
                            reel_description="Reel caption", video_content="Video body")
    db.create_page_schedule(1, [page_id], iso(utcnow() - timedelta(hours=1)))

    assert db.expire_missed_page_schedules() == 1
    assert db.claim_due_page_schedule() is None
    assert "không tự đăng bù" in db.list_page_schedules(1)[0]["error"]


def test_bulk_save_is_atomic_when_one_page_is_invalid(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    db.ensure_page_targets("FB_01", [
        {"facebook_id": "101", "name": "First", "url": "https://facebook.com/101"},
        {"facebook_id": "102", "name": "Second", "url": "https://facebook.com/102"},
    ])
    first, second = db.list_page_targets(1)
    rows = [
        {"id": first["id"], "facebook_id": "101", "source_url": "https://example.com/one",
         "reel_description": "First caption", "video_content": "First video", "article_language": "en"},
        {"id": second["id"], "facebook_id": "wrong-id", "source_url": "https://example.com/two",
         "reel_description": "Second caption", "video_content": "Second video", "article_language": "en"},
    ]
    try:
        db.bulk_save_page_sources(1, rows)
        assert False, "expected invalid Page"
    except ValueError as exc:
        assert "không còn thuộc" in str(exc)
    assert all(not page["source_url"] for page in db.list_page_targets(1))


def test_page_scan_sync_replaces_membership_but_preserves_stale_data(tmp_path):
    db = make_db(tmp_path)
    db.ensure_default_profiles(1)
    profile = db.list_profiles()[0]
    db.ensure_page_targets(
        "FB_01",
        [{"facebook_id": "old", "name": "Old Page", "url": "https://facebook.com/old"}],
    )
    old_page = db.list_page_targets(profile["id"])[0]
    db.update_page_workflow(old_page["id"], source_url="https://example.com/kept")

    pages = db.sync_page_targets(profile["id"], [
        {"facebook_id": "new", "name": "New Page", "url": "https://facebook.com/new"},
        {"facebook_id": "new", "name": "New Page", "url": "https://facebook.com/new"},
    ])

    assert [page["facebook_id"] for page in pages] == ["new"]
    preserved = next(page for page in db.list_page_targets() if page["facebook_id"] == "old")
    assert preserved["profile_id"] is None
    assert preserved["source_url"] == "https://example.com/kept"


def test_hidemyacc_folder_sync_creates_profile_hierarchy(tmp_path):
    db = make_db(tmp_path)
    request = db.create_sync_request("ACC_AUTO_FB")
    agent = db.upsert_agent({"name": "sync-agent", "version": "test"})
    claimed = db.claim_sync_request(agent["id"])
    assert claimed["id"] == request["id"]

    assert db.finish_sync_request(
        request["id"],
        agent["id"],
        "completed",
        [{"hidemyacc_id": "hma-fb-03", "name": "FB_03"}],
    )
    profiles = db.list_profiles()
    assert profiles[0]["name"] == "FB_03"
    assert profiles[0]["folder_name"] == "ACC_AUTO_FB"
    assert profiles[0]["source"] == "hidemyacc"
    assert db.list_sync_requests()[0]["found_count"] == 1
