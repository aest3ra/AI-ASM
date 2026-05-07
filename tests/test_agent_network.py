import asyncio

from ai_asm.agent.network import NetworkEventBuffer


def test_network_event_buffer_returns_append_only_records_after_cursor():
    buffer = NetworkEventBuffer()
    buffer.record(method="GET", url="https://example.com/api/poll")
    cursor = buffer.cursor()

    buffer.record(method="GET", url="https://example.com/api/poll")

    records = buffer.since(cursor)
    assert len(records) == 1
    assert records[0]["method"] == "GET"
    assert records[0]["url"] == "https://example.com/api/poll"


def test_network_event_buffer_wait_after_returns_when_request_arrives():
    async def run():
        buffer = NetworkEventBuffer()
        cursor = buffer.cursor()

        async def delayed_record():
            await asyncio.sleep(0.01)
            buffer.record(method="POST", url="https://example.com/api/login")

        task = asyncio.create_task(delayed_record())
        await buffer.wait_after(cursor, timeout_ms=500)
        await task

        assert buffer.since(cursor)[0]["url"] == "https://example.com/api/login"

    asyncio.run(run())
