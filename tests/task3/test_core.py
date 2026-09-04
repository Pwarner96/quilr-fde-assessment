from __future__ import annotations

import json
import random

import pytest

from fde_assessment.task3_stream_guardrail.core import (
    MAX_EVENT_BYTES,
    REDACTION,
    BoundedPIIRedactor,
    GuardrailError,
    SSEEventFramer,
    StreamingGuardrailCore,
    StrictUTF8Decoder,
    stream_core,
)


def packet(content: str, finish: str | None = None) -> bytes:
    choice: dict[str, object] = {"delta": {"content": content}}
    if finish is not None:
        choice["finish_reason"] = finish
    return ("data: " + json.dumps({"choices": [choice]}) + "\n\n").encode()


def test_utf8_every_byte_boundary_and_invalid_input() -> None:
    value = "café ☕"
    encoded = value.encode()
    for split in range(len(encoded) + 1):
        decoder = StrictUTF8Decoder()
        assert (
            decoder.feed(encoded[:split])
            + decoder.feed(encoded[split:])
            + decoder.finish()
            == value
        )
    with pytest.raises(GuardrailError):
        StrictUTF8Decoder().feed(b"\xff")
    decoder = StrictUTF8Decoder()
    decoder.feed("é".encode()[:1])
    with pytest.raises(GuardrailError):
        decoder.finish()


def test_sse_newline_forms_comments_and_multiple_data_fields() -> None:
    for newline in ("\n", "\r", "\r\n"):
        framer = SSEEventFramer()
        assert framer.feed(": ignored" + newline + "data: {" + newline) == []
        assert framer.feed('data: "x": 1}' + newline + newline) == ['{\n"x": 1}']
        assert framer.finish() == []


def test_sse_split_crlf_and_event_bound() -> None:
    framer = SSEEventFramer()
    assert framer.feed("data: x\r") == []
    assert framer.feed("\n") == []
    assert framer.feed("\r") == ["x"]
    assert framer.feed("\n") == []
    assert framer.finish() == []

    exact = SSEEventFramer()
    assert exact.feed("x" * MAX_EVENT_BYTES) == []
    with pytest.raises(GuardrailError):
        exact.feed("\r")

    for ending in ("\n", "\r", "\r\n"):
        bounded = SSEEventFramer()
        assert bounded.feed("x" * (MAX_EVENT_BYTES - len(ending))) == []
        if ending == "\r\n":
            assert bounded.feed("\r") == []
            assert bounded.feed("\n") == []
        else:
            assert bounded.feed(ending) == []
        with pytest.raises(GuardrailError):
            bounded.feed("x")


def test_redactor_split_sensitive_values_and_near_miss() -> None:
    values = [
        "paul@example.com",
        "123456789",
        "123-45-6789",
        "4111111111111111",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
    ]
    expected = [REDACTION] * len(values)
    for value, replacement in zip(values, expected):
        for split in range(1, len(value)):
            redactor = BoundedPIIRedactor()
            result = (
                redactor.feed(value[:split])
                + redactor.feed(value[split:])
                + redactor.finish()
            )
            assert result == [replacement]
    near_miss = BoundedPIIRedactor()
    assert near_miss.feed("4111 1111 1111 1112") == []
    assert near_miss.finish() == ["4111 1111 1111 1112"]

    unicode_digits = BoundedPIIRedactor()
    assert unicode_digits.feed("１２３４５６７８９") == []
    assert unicode_digits.finish() == ["１２３４５６７８９"]


@pytest.mark.parametrize(
    "text", ["123 abc", "value 4111 1111 1111 1112", "near 12345678 end"]
)
def test_numeric_near_misses_preserve_all_text_across_splits(text: str) -> None:
    for split in range(1, len(text)):
        redactor = BoundedPIIRedactor()
        output = (
            redactor.feed(text[:split])
            + redactor.feed(text[split:])
            + redactor.finish()
        )
        assert "".join(output) == text


def test_numeric_candidate_exact_bound_and_overflow_drain() -> None:
    redactor = BoundedPIIRedactor()
    assert redactor.feed("1" * 64) == []
    assert redactor.pending_length == 64
    assert redactor.feed("1") == [REDACTION]
    assert redactor.pending_length == 0
    assert redactor.draining
    assert redactor.feed("234567890123456789 ") == [" "]
    assert redactor.finish() == []


def test_full_core_orders_text_finish_and_done_and_redacts_end() -> None:
    source = (
        packet("Contact paul@exa") + packet("mple.com", "stop") + b"data: [DONE]\n\n"
    )
    output = stream_core([source[:11], source[11:29], source[29:]])
    assert output[-1] == "data: [DONE]\n\n"
    assert '"finish_reason":"stop"' in output[-2]
    assert "[REDACTED]" in "".join(output)
    assert "paul@example.com" not in "".join(output)


def test_done_flushes_pending_state_once_and_rejects_following_content() -> None:
    done = b"data: [DONE]\n\n"
    for value in ("safe", "paul@example.com", "123-45-6789", "4111111111111111"):
        core = StreamingGuardrailCore()
        assert core.feed(packet(value)) == []
        output = core.feed(done)
        assert output[-1] == "data: [DONE]\n\n"
        assert sum("[REDACTED]" in part for part in output) <= 1
        assert core.feed(packet("after")) == []
        assert core.feed(done) == []
        assert core.finish() == []


def test_strict_json_rejects_duplicates_nan_unknown_and_bad_index() -> None:
    from fde_assessment.task3_stream_guardrail.core import ChatCompletionsDeltaParser

    parser = ChatCompletionsDeltaParser()
    for value in (
        '{"choices":[],"choices":[]}',
        '{"choices":[{"delta":{"content":"x"}}],"created":NaN}',
        '{"choices":[{"index":1,"delta":{"content":"x"}}]}',
        '{"choices":[{"delta":{"content":1}}]}',
        '{"choices":[{"index":false,"delta":{"content":"x"}}]}',
        '{"choices":[{"delta":{"role":"user","content":"x"}}]}',
    ):
        with pytest.raises(GuardrailError):
            parser.parse(value)


def test_missing_done_is_abnormal_and_never_flushes_or_synthesizes() -> None:
    for source in (packet("safe"), packet("hidden@sample.com", "stop"), b"data: {\n"):
        core = StreamingGuardrailCore()
        core.feed(source)
        assert core.finish() == []
        assert core.feed(b"data: [DONE]\n\n") == []


def test_abnormal_partial_event_and_candidate_emit_nothing_unsafe() -> None:
    core_input = packet("hidden@sample.com")[:-2]
    assert stream_core([core_input]) == []
    redactor = BoundedPIIRedactor()
    redactor.feed("hidden@sample.com")
    assert redactor.finish(normal=False) == []


def test_seeded_partitions_and_lazy_stream_keep_state_bounded(caplog: object) -> None:
    rng = random.Random(3)
    values = [
        "paul@example.com",
        "123456789",
        "123-45-6789",
        "4111111111111111",
        "not-sensitive",
    ]
    for value in values:
        cuts = sorted(rng.sample(range(1, len(value)), min(3, len(value) - 1)))
        pieces: list[str] = []
        start = 0
        for cut in cuts + [len(value)]:
            pieces.append(value[start:cut])
            start = cut
        redactor = BoundedPIIRedactor()
        result = [
            part for piece in pieces for part in redactor.feed(piece)
        ] + redactor.finish()
        assert "".join(result) in {REDACTION, value}

    core = StreamingGuardrailCore()
    lazy = (packet("x") for _ in range(200))
    for fragment in lazy:
        core.feed(fragment)
        assert core.pending_bytes <= MAX_EVENT_BYTES
        assert core.pending_candidate_length <= 320
    assert not hasattr(core, "_history")
    assert not hasattr(core, "_response")
    assert "paul@example.com" not in str(caplog)
