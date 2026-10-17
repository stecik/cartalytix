from os import environ
from uuid import uuid4

import pytest
from redis import Redis

from cartalytix.main import RedisLoginLimiter
from cartalytix.security import token_hash
from cartalytix.settings import Settings


def test_redis_login_limit_is_shared_and_has_expiry() -> None:
    url = environ.get("REDIS_URL") or environ.get("CARTALYTIX_TEST_REDIS_URL")
    if not url:
        pytest.skip("No isolated Redis integration service configured")
    if environ.get("CARTALYTIX_ENVIRONMENT") != "test":
        pytest.fail("Redis integration requires CARTALYTIX_ENVIRONMENT=test")
    redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    identity = f"integration-{uuid4().hex}"
    settings = Settings(environment="test", login_attempts=2)
    first = RedisLoginLimiter(redis, settings)
    second = RedisLoginLimiter(redis, settings)
    try:
        assert first.check(identity, identity)
        assert second.check(identity, identity)
        assert not first.check(identity, identity)
        ttl = redis.ttl(f"login:ip:{token_hash(identity)}")
        assert isinstance(ttl, int)
        assert 0 < ttl <= settings.login_window_seconds
        # Per-identity isolation: different users/peers have an independent budget.
        assert first.check(f"{identity}-other", f"{identity}-other")
    finally:
        redis.delete(
            *(
                f"login:{kind}:{token_hash(value)}"
                for kind in ["ip", "email"]
                for value in [identity, f"{identity}-other"]
            )
        )
        redis.close()
