from redis import Redis
from redis.exceptions import ResponseError


def redis_client(settings):
    return Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3,
                          socket_timeout=5, health_check_interval=15)


def ensure_group(redis, settings):
    try:
        redis.xgroup_create(settings.stream, settings.group, id="0-0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def read_message(redis, settings, consumer, cursor="0-0"):
    ensure_group(redis, settings)
    claimed = redis.xautoclaim(settings.stream, settings.group, consumer,
                              settings.reclaim_idle_ms, start_id=cursor, count=1)
    cursor = claimed[0]
    if claimed[1]:
        return claimed[1][0], cursor
    messages = redis.xreadgroup(settings.group, consumer, {settings.stream: ">"}, count=1, block=1000)
    return (messages[0][1][0] if messages else None), cursor
