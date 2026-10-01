from apps.dashboard.auth import create_session, valid_session


def test_signed_session(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret")
    token = create_session(ttl_seconds=30)
    assert valid_session(token)
    assert not valid_session(token + "x")


def test_expired_session(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret")
    token = create_session(ttl_seconds=-1)
    assert not valid_session(token)
