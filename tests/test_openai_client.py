import asyncio
import json

from ai_asm.agent.client import OpenAIClient, _compact_context, load_openai_api_key


class FakeUsageDetails:
    cached_tokens = 4


class FakeUsage:
    input_tokens = 12
    output_tokens = 3
    input_tokens_details = FakeUsageDetails()


class FakeFunctionCall:
    type = "function_call"
    call_id = "call-1"
    name = "click_ref"
    arguments = '{"ref": "r1"}'


class FakeMessageText:
    type = "output_text"
    text = "ok"


class FakeMessage:
    type = "message"
    content = [FakeMessageText()]


class FakeResponse:
    output = [FakeFunctionCall(), FakeMessage()]
    output_text = "ok"
    usage = FakeUsage()


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeOpenAI:
    def __init__(self):
        self.responses = FakeResponses()


class RetryableError(Exception):
    status_code = 500


class FakeFlakyResponses:
    def __init__(self):
        self.calls = 0
        self.kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.calls < 3:
            raise RetryableError("server error")
        return FakeResponse()


class FakeFlakyOpenAI:
    def __init__(self):
        self.responses = FakeFlakyResponses()


class UnsupportedTemperatureError(Exception):
    status_code = 400


class FakeUnsupportedTemperatureResponses:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise UnsupportedTemperatureError(
                "Unsupported parameter: 'temperature' is not supported with this model.",
            )
        return FakeResponse()


class FakeUnsupportedTemperatureOpenAI:
    def __init__(self):
        self.responses = FakeUnsupportedTemperatureResponses()


def test_openai_client_maps_responses_function_calls():
    async def run():
        fake = FakeOpenAI()

        result = await OpenAIClient(
            client=fake,
            model="gpt-test",
            system_prompt="system",
        ).complete({"snapshot": {"refs": []}})

        assert result.tool_calls[0].name == "click_ref"
        assert result.tool_calls[0].arguments == {"ref": "r1"}
        assert result.input_tokens == 12
        assert result.output_tokens == 3
        assert result.cache_read_input_tokens == 4
        assert fake.responses.kwargs["model"] == "gpt-test"
        assert fake.responses.kwargs["tool_choice"] == "required"
        assert fake.responses.kwargs["temperature"] == 0.0
        assert fake.responses.kwargs["tools"][0]["type"] == "function"
        assert "reason" in fake.responses.kwargs["tools"][0]["parameters"]["properties"]

    asyncio.run(run())


def test_openai_client_retries_without_temperature_when_model_rejects_it():
    async def run():
        fake = FakeUnsupportedTemperatureOpenAI()

        result = await OpenAIClient(
            client=fake,
            model="gpt-5-mini",
            system_prompt="system",
            max_retries=0,
        ).complete({"snapshot": {"refs": []}})

        assert result.tool_calls[0].name == "click_ref"
        assert len(fake.responses.calls) == 2
        assert fake.responses.calls[0]["temperature"] == 0.0
        assert "temperature" not in fake.responses.calls[1]

    asyncio.run(run())


def test_openai_client_retries_retryable_errors():
    async def run():
        fake = FakeFlakyOpenAI()

        result = await OpenAIClient(
            client=fake,
            model="gpt-test",
            system_prompt="system",
            max_retries=3,
            retry_base_delay=0,
        ).complete({"snapshot": {"refs": []}})

        assert result.tool_calls[0].name == "click_ref"
        assert fake.responses.calls == 3

    asyncio.run(run())


def test_load_openai_api_key_reads_env_file_without_exposing_value(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=secret-value\n")

    assert load_openai_api_key(env) == "secret-value"


def test_compact_context_limits_snapshot_refs_and_fields():
    context = {
        "page_url": "https://example.com/",
        "visible_forms": [{"form_ref": "f1", "kind": "login"}],
        "exploration_status": {"should_give_up": False},
        "snapshot": {
            "url": "https://example.com/",
            "refs": [
                {
                    "ref": f"r{idx}",
                    "tag": "button",
                    "text": "Open",
                    "unused": "x" * 100,
                    "input_fields": [
                        {"name": f"field{field}", "type": "text", "unused": "y"}
                        for field in range(12)
                    ],
                }
                for idx in range(100)
            ],
        },
    }

    compact = _compact_context(context)

    assert compact["snapshot"]["ref_count"] == 100
    assert compact["visible_forms"] == [{"form_ref": "f1", "kind": "login"}]
    assert compact["exploration_status"] == {"should_give_up": False}
    assert len(compact["snapshot"]["refs"]) == 80
    assert "unused" not in compact["snapshot"]["refs"][0]
    assert len(compact["snapshot"]["refs"][0]["input_fields"]) == 8
    json.dumps(compact)
