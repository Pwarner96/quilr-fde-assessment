from fde_assessment.task3_stream_guardrail.email_redactor import (
    MAX_EMAIL_CHARS,
    REDACTION,
    EmailRedactor,
)

EMAIL = "paul@example.com"
EXPECTED = "Contact [REDACTED] now"


def run_fragments(fragments: list[str]) -> str:
    redactor = EmailRedactor()
    output = [part for fragment in fragments for part in redactor.feed(fragment)]
    output.extend(redactor.finish())
    return "".join(output)


def test_required_cross_fragment_sequence_and_early_safe_prefix() -> None:
    redactor = EmailRedactor()

    first = redactor.feed("Contact paul@exa")
    assert "".join(first) == "Contact "
    assert redactor.pending_length == len("paul@exa")
    assert redactor.pending_length <= MAX_EMAIL_CHARS

    second = redactor.feed("mple.com now")
    assert "".join(second) == f"{REDACTION} "
    assert redactor.pending_length == len("now")
    assert redactor.finish() == ["now"]


def test_every_two_way_split_has_same_redacted_result() -> None:
    source = "Contact " + EMAIL + " now"
    for split in range(1, len(source)):
        assert run_fragments([source[:split], source[split:]]) == EXPECTED


def test_selected_multiway_and_one_character_fragments() -> None:
    source = "Contact " + EMAIL + " now"
    assert run_fragments(list(source)) == EXPECTED
    assert (
        run_fragments(
            [source[:8], "p", "aul@", "ex", "ample", ".com", " ", "n", "o", "w"]
        )
        == EXPECTED
    )


def test_safe_non_email_text_is_preserved() -> None:
    assert (
        run_fragments(["plain", " text", " without", " email"])
        == "plain text without email"
    )


def test_overflow_replaces_once_and_drains_in_constant_memory() -> None:
    redactor = EmailRedactor()
    first = redactor.feed("a" * 319 + "@")
    assert first == []
    assert redactor.pending_length == 320

    overflow = redactor.feed("x")
    assert overflow == [REDACTION]
    assert redactor.pending_length == 0
    assert redactor.draining
    assert redactor.feed("secretmoretext") == []
    assert redactor.feed(" ") == [" "]
    assert not redactor.draining
    assert redactor.finish() == []


def test_overflow_before_at_never_emits_a_raw_prefix() -> None:
    redactor = EmailRedactor()

    assert redactor.feed("a" * MAX_EMAIL_CHARS) == []
    overflow = redactor.feed("a")
    assert overflow == [REDACTION]
    assert redactor.pending_length == 0
    assert redactor.draining

    # The discarded suffix later becomes email-shaped, but cannot cause a second
    # replacement or expose any prefix of the overlong candidate.
    assert redactor.feed("@example.com ") == [" "]
    assert redactor.finish() == []


def test_normal_finish_resolves_candidate_and_abnormal_finish_discards_it() -> None:
    normal = EmailRedactor()
    normal.feed("hidden@sample.com")
    assert normal.finish() == [REDACTION]

    abnormal = EmailRedactor()
    abnormal.feed("hidden@sample.com")
    assert abnormal.finish(normal=False) == []
    assert abnormal.pending_length == 0


def test_state_has_no_response_or_history_accumulator() -> None:
    redactor = EmailRedactor()
    assert not hasattr(redactor, "_history")
    assert not hasattr(redactor, "_response")
    assert not hasattr(redactor, "_chunks")
    redactor.feed("hidden@sample.com")
    assert redactor.pending_length == len("hidden@sample.com")


def test_no_content_is_logged(caplog: object) -> None:
    redactor = EmailRedactor()
    redactor.feed("hidden@sample.com")
    redactor.finish()
    assert "hidden@sample.com" not in str(caplog)
