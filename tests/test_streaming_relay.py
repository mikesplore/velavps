import asyncio
import base64

from app.services.vela_forwarder import decode_chunk_body
from app.services.vela_agent_registry import StreamRelaySession


def test_decode_chunk_body_utf8():
    assert decode_chunk_body({"body": "hello", "body_encoding": "utf-8"}) == b"hello"


def test_decode_chunk_body_base64():
    payload = b"\x00\x01\xff"
    encoded = base64.b64encode(payload).decode("ascii")
    assert decode_chunk_body({"body": encoded, "body_encoding": "base64"}) == payload


def test_stream_relay_session_start_and_chunks():
    async def _run():
        session = StreamRelaySession()
        assert not session.started.is_set()
        session.status_code = 200
        session.headers = {"content-type": "text/event-stream"}
        session.started.set()
        await session.chunks.put(b"data: 1\n\n")
        await session.chunks.put(b"data: 2\n\n")
        await session.chunks.put(None)

        assert session.started.is_set()
        assert await session.chunks.get() == b"data: 1\n\n"
        assert await session.chunks.get() == b"data: 2\n\n"
        assert await session.chunks.get() is None

    asyncio.run(_run())
