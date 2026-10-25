"""Pomocnik „Jak to działa?”: tryb FAQ, wywołanie Claude (atrapa API, bez sieci), API."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from termin.assistant import (
    REFUSAL_ANSWER,
    UNKNOWN_ANSWER,
    Assistant,
    Turn,
    clean_history,
    faq_answer,
    match_topic,
    system_prompt,
    topics,
)
from termin.config import Settings
from termin.logs import SENSITIVE_KEYS
from termin.runtime import Transports
from termin.web.app import create_app
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz


class FakeAnthropic:
    """Udaje `POST /v1/messages` Anthropic API i zapamiętuje zapytania."""

    def __init__(
        self,
        text: str = "Kliknij „Zainstaluj” na górze strony.",
        *,
        status: int = 200,
        stop_reason: str = "end_turn",
    ) -> None:
        self.text = text
        self.status = status
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        if self.status != 200:
            return httpx2.Response(
                self.status,
                json={"type": "error", "error": {"type": "api_error", "message": "x"}},
            )
        content = [] if self.stop_reason == "refusal" else [{"type": "text", "text": self.text}]
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": content,
                "stop_reason": self.stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 1200, "output_tokens": 40},
            },
        )


def ai_settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(data_dir=tmp_path, ai_api_key=SecretStr("sk-ant-test"), **extra)


# ------------------------------------------------------------------ baza wiedzy i tryb FAQ


def test_every_topic_has_keywords_and_answer() -> None:
    ids = [t.id for t in topics()]
    assert len(ids) == len(set(ids)) >= 20
    for topic in topics():
        assert topic.keywords and topic.answer
        for kw in topic.keywords:
            assert kw == kw.lower() and not set(kw) & set("ąćęłńóśźż"), kw


@pytest.mark.parametrize(
    ("question", "topic"),
    [
        ("Jak zainstalować aplikację na telefonie?", "install"),
        ("Co znaczy prognoza 1 mies. 3 tyg.?", "forecast"),
        ("Zapomniałem hasła", "password"),
        ("Jak się zapisać do kardiologa?", "cer"),
        ("Nie ma żadnych wyników", "no_results"),
        ("Jak włączyć 2FA?", "twofa"),
        ("Jak obserwować termin?", "watch"),
        ("Co powiedzieć, jak zadzwonię?", "call"),
        ("Szukam dla dziecka", "children"),
        ("Boli mnie głowa, do jakiego lekarza?", "medical"),
        ("Wynik badań krwi: co znaczy?", "medical"),
        ("Gdzie jest mapa?", "distance"),
    ],
)
def test_faq_matches_topic(question: str, topic: str) -> None:
    found = match_topic(question)
    assert found is not None and found.id == topic


def test_faq_unknown_question() -> None:
    answer = faq_answer("qwerty asdf")
    assert answer == faq_answer("qwerty asdf")
    assert answer.reply == UNKNOWN_ANSWER and answer.mode == "faq"


def test_medical_answer_gives_no_advice() -> None:
    reply = faq_answer("jakie leki na ból brzucha").reply
    assert "Nie udzielam porad medycznych" in reply and "112" in reply


def test_system_prompt_contains_rules_and_knowledge() -> None:
    prompt = system_prompt("en")
    assert "po angielsku" in prompt and "{lang}" not in prompt
    assert "Nie udzielasz porad medycznych" in prompt
    assert all(t.answer in prompt for t in topics())


def test_clean_history_keeps_recent_turns_starting_with_question() -> None:
    turns = [Turn("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(11)]
    cleaned = clean_history(turns)
    assert cleaned[0].role == "user" and cleaned[-1].content == "m10"
    assert len(cleaned) <= 8


def test_logs_redact_chat_content() -> None:
    assert {"messages", "question", "reply", "content", "ai_api_key"} <= SENSITIVE_KEYS


def test_anthropic_api_key_env_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    key = Settings().ai_api_key
    assert key is not None and key.get_secret_value() == "sk-ant-env"
    assert "sk-ant-env" not in repr(Settings())


# ------------------------------------------------------------------ tryb AI (atrapa API)


async def test_without_key_uses_faq(tmp_path: Path) -> None:
    helper = Assistant(Settings(data_dir=tmp_path))
    assert not helper.ai_available
    answer = await helper.answer([Turn("user", "jak zainstalować aplikację")])
    assert answer.mode == "faq" and answer.topic == "install"


async def test_ai_answer_request_shape(tmp_path: Path) -> None:
    fake = FakeAnthropic()
    helper = Assistant(ai_settings(tmp_path), transport=fake.transport())
    turns = [
        Turn("user", "gdzie jest mapa?"),
        Turn("assistant", "Nad wynikami."),
        Turn("user", "a jak zainstalować?"),
    ]
    answer = await helper.answer(turns, "uk")
    await helper.aclose()
    assert answer.mode == "ai" and answer.reply.startswith("Kliknij")
    body = fake.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["messages"][-1] == {"role": "user", "content": "a jak zainstalować?"}
    assert "po ukraińsku" in body["system"]
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body  # Opus 5.5: myślenie zawsze włączone, sterujemy przez effort
    assert "server-side-fallback-2026-07-01" in fake.headers[0]["anthropic-beta"]
    assert fake.headers[0]["authorization"] == "Bearer sk-ant-test"


async def test_haiku_has_no_server_side_fallback(tmp_path: Path) -> None:
    fake = FakeAnthropic()
    settings = ai_settings(tmp_path, ai_model="claude-haiku-5-5")
    helper = Assistant(settings, transport=fake.transport())
    await helper.answer([Turn("user", "pytanie")])
    await helper.aclose()
    assert "fallbacks" not in fake.requests[0]
    assert "anthropic-beta" not in fake.headers[0]


async def test_refusal_returns_polite_answer(tmp_path: Path) -> None:
    fake = FakeAnthropic(stop_reason="refusal")
    helper = Assistant(ai_settings(tmp_path), transport=fake.transport())
    answer = await helper.answer([Turn("user", "coś")])
    assert answer.reply == REFUSAL_ANSWER


@pytest.mark.parametrize("status", [400, 401, 429, 529])
async def test_api_error_falls_back_to_faq(tmp_path: Path, status: int) -> None:
    fake = FakeAnthropic(status=status)
    helper = Assistant(ai_settings(tmp_path), transport=fake.transport())
    answer = await helper.answer([Turn("user", "zapomniałem hasła")])
    assert answer.mode == "faq" and answer.topic == "password"


async def test_daily_limit_switches_to_faq_and_resets(tmp_path: Path) -> None:
    fake = FakeAnthropic()
    day = [dt.date(2026, 10, 24)]
    helper = Assistant(
        ai_settings(tmp_path, ai_daily_limit=2), transport=fake.transport(), today=lambda: day[0]
    )
    modes = [(await helper.answer([Turn("user", "hasło")])).mode for _ in range(3)]
    assert modes == ["ai", "ai", "faq"]
    day[0] = dt.date(2026, 10, 25)
    assert (await helper.answer([Turn("user", "hasło")])).mode == "ai"
    assert len(fake.requests) == 3


async def test_question_must_be_last(tmp_path: Path) -> None:
    helper = Assistant(ai_settings(tmp_path), transport=FakeAnthropic().transport())
    answer = await helper.answer([Turn("assistant", "Dzień dobry")])
    assert answer.reply == UNKNOWN_ANSWER


# ------------------------------------------------------------------ API


def make_client(
    tmp_path: Path, settings: Settings, fake: FakeAnthropic | None = None
) -> TestClient:
    app = create_app(
        settings,
        transports=Transports(
            nfz=FakeNfz.with_defaults().transport(), ai=fake.transport() if fake else None
        ),
        today=lambda: TODAY,
    )
    return TestClient(app, base_url="https://termin.example")


@pytest.fixture
def faq_client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path, Settings(data_dir=tmp_path, geocoder="offline")) as c:
        yield c


def test_api_faq_mode(faq_client: TestClient) -> None:
    assert faq_client.get("/api/config").json()["assistant"] == {"enabled": True, "ai": False}
    assert faq_client.get("/api/assistant").json() == {"enabled": True, "ai": False}
    res = faq_client.post(
        "/api/assistant", json={"messages": [{"role": "user", "content": "jak zainstalować?"}]}
    )
    assert res.status_code == 200
    assert res.json()["mode"] == "faq" and res.json()["topic"] == "install"


def test_api_validates_input(faq_client: TestClient) -> None:
    long = faq_client.post(
        "/api/assistant", json={"messages": [{"role": "user", "content": "x" * 501}]}
    )
    assert long.status_code == 400
    last = faq_client.post(
        "/api/assistant",
        json={
            "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        },
    )
    assert last.status_code == 400
    assert faq_client.post("/api/assistant", json={"messages": []}).status_code == 422
    bad_role = faq_client.post(
        "/api/assistant", json={"messages": [{"role": "system", "content": "a"}]}
    )
    assert bad_role.status_code == 422


def test_api_rate_limit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="offline", ai_rate_limit="2/minute")
    with make_client(tmp_path, settings) as c:
        payload = {"messages": [{"role": "user", "content": "hasło"}]}
        codes = [c.post("/api/assistant", json=payload).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_api_ai_mode(tmp_path: Path) -> None:
    fake = FakeAnthropic()
    settings = ai_settings(tmp_path, geocoder="offline")
    with make_client(tmp_path, settings, fake) as c:
        assert c.get("/api/assistant").json()["ai"] is True
        res = c.post(
            "/api/assistant",
            json={"messages": [{"role": "user", "content": "jak zainstalować?"}], "lang": "en"},
        )
    assert res.json()["mode"] == "ai"
    assert "po angielsku" in fake.requests[0]["system"]


def test_api_disabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="offline", ai_enabled=False)
    with make_client(tmp_path, settings) as c:
        assert c.get("/api/config").json()["assistant"]["enabled"] is False
        res = c.post("/api/assistant", json={"messages": [{"role": "user", "content": "a"}]})
    assert res.status_code == 404


def test_privacy_policy_names_anthropic_only_with_key(tmp_path: Path) -> None:
    from termin.web.legal import render_legal

    assert "Anthropic PBC" in render_legal("prywatnosc", ai_settings(tmp_path))
    without = render_legal("prywatnosc", Settings(data_dir=tmp_path))
    assert "Anthropic" not in without and "Pomocnik" in without
    assert "$assistant" not in without
