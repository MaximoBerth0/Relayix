"""wiring check for the fixtures: DB, Redis and the HTTP client all come up"""

from sqlalchemy import text


async def test_db_session_reaches_postgres(db_session):
    assert await db_session.scalar(text("SELECT 1")) == 1


async def test_redis_client_reaches_redis(redis_client):
    assert await redis_client.ping() is True


async def test_client_serves_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
