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
