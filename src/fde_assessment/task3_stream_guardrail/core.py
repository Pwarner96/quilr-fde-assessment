"""Pure bounded byte/SSE/delta/redaction core for Task 3."""

from __future__ import annotations

import codecs
import json
import re
from collections.abc import Iterable

REDACTION = "[REDACTED]"
MAX_EVENT_BYTES = 65_536
MAX_EMAIL_CHARS = 320
MAX_NUMERIC_CHARS = 64

_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_SSN = re.compile(r"(?:[0-9]{9}|[0-9]{3}-[0-9]{2}-[0-9]{4})")
_TOKEN_DELIMITERS = frozenset(' \t\r\n,;:!?()[]{}<>"\\')


class GuardrailError(ValueError):
    """An unsafe or malformed stream condition."""


class BoundedPIIRedactor:
    """Conservative token scanner for email, SSN, and Luhn-valid card text."""

    __slots__ = ("_draining", "_pending", "_replacement_emitted")

    def __init__(self) -> None:
        self._pending = ""
        self._draining = False
        self._replacement_emitted = False

    @property
    def pending_length(self) -> int:
        return len(self._pending)

    @property
    def draining(self) -> bool:
        return self._draining

    def feed(self, text: str) -> list[str]:
        output: list[str] = []
        for char in text:
            if self._draining:
                if char in _TOKEN_DELIMITERS:
                    self._draining = False
                    self._replacement_emitted = False
                    output.append(char)
                continue
            if char == " " and self._card_like():
                self._pending += char
                continue
            if char in _TOKEN_DELIMITERS:
                output.extend(self._resolve())
                output.append(char)
                continue
            if (
                self._pending
                and self._card_like()
                and not (char.isascii() and (char.isdigit() or char in "- "))
            ):
                output.extend(self._resolve())
            self._pending += char
            limit = MAX_NUMERIC_CHARS if self._card_like() else MAX_EMAIL_CHARS
            if len(self._pending) > limit:
                output.append(REDACTION)
                self._pending = ""
                self._draining = True
                self._replacement_emitted = True
        return output

    def finish(self, normal: bool = True) -> list[str]:
        if not normal or self._draining:
            self._clear()
            return []
        output = self._resolve()
        self._clear()
        return output

    def _numeric_like(self) -> bool:
        return bool(self._pending) and all(
            char.isascii() and (char.isdigit() or char == "-") for char in self._pending
        )

    def _card_like(self) -> bool:
        return bool(self._pending) and all(
            char.isascii() and (char.isdigit() or char in "- ")
            for char in self._pending
        )

    def _resolve(self) -> list[str]:
        value = self._pending
        self._pending = ""
        trailing_spaces = value[len(value.rstrip(" ")) :]
        value = value.rstrip(" ")
        terminal_period = value.endswith(".")
        candidate = value[:-1] if terminal_period else value
        if (
            _EMAIL.fullmatch(candidate)
            or _SSN.fullmatch(candidate)
            or _valid_card(candidate)
        ):
            result = [REDACTION, "."] if terminal_period else [REDACTION]
            if trailing_spaces:
                result.append(trailing_spaces)
            return result
        return [value + trailing_spaces] if value or trailing_spaces else []

    def _clear(self) -> None:
        self._pending = ""
        self._draining = False
        self._replacement_emitted = False


def _valid_card(value: str) -> bool:
    digits = value.replace("-", "").replace(" ", "")
    if not 13 <= len(digits) <= 19 or not all(char in "0123456789" for char in digits):
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        number = int(digit)
        if index % 2 == parity:
            number = number * 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


class StrictUTF8Decoder:
    __slots__ = ("_decoder",)

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")

    def feed(self, fragment: bytes) -> str:
        try:
            return self._decoder.decode(fragment, final=False)
        except UnicodeDecodeError as exc:
            raise GuardrailError("invalid UTF-8") from exc

    def finish(self) -> str:
        try:
            return self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise GuardrailError("incomplete UTF-8") from exc


class SSEEventFramer:
    """Frame SSE events while enforcing the byte bound during accumulation."""

    __slots__ = ("_data", "_event_bytes", "_line", "_line_bytes", "_pending_cr")

    def __init__(self) -> None:
        self._data: list[str] = []
        self._event_bytes = 0
        self._line = ""
        self._line_bytes = 0
        self._pending_cr = False

    @property
    def pending_bytes(self) -> int:
        return self._event_bytes + self._line_bytes

    def feed(self, text: str) -> list[str]:
        events: list[str] = []
        for char in text:
            if self._pending_cr:
                self._pending_cr = False
                if char == "\n":
                    self._count(1)
                    if self._event_bytes:
                        self._event_bytes += 1
                    continue
            if char == "\r":
                self._count(1)
                self._finish_line(events)
                self._pending_cr = True
            elif char == "\n":
                self._count(1)
                self._finish_line(events)
            else:
                size = len(char.encode("utf-8"))
                self._count(size)
                self._line += char
                self._line_bytes += size
        return events

    def finish(self) -> list[str]:
        if self._pending_cr:
            self._pending_cr = False
        if self._line or self._line_bytes:
            raise GuardrailError("incomplete SSE line")
        if self._data or self._event_bytes:
            raise GuardrailError("incomplete SSE event")
        return []

    def _count(self, size: int) -> None:
        if self._event_bytes + self._line_bytes + size > MAX_EVENT_BYTES:
            raise GuardrailError("SSE event exceeds bound")

    def _finish_line(self, events: list[str]) -> None:
        line = self._line
        self._line = ""
        self._line_bytes = 0
        if not line:
            if self._data:
                events.append("\n".join(self._data))
                self._data = []
            self._event_bytes = 0
        elif line.startswith(":"):
            pass
        elif line.startswith("data:"):
            value = line[5:]
            self._data.append(value.removeprefix(" "))
        if line:
            self._event_bytes += len(line.encode("utf-8")) + 1


class ChatCompletionsDeltaParser:
    __slots__ = ()

    def parse(
        self, data: str
    ) -> tuple[str | None, str | None, dict[str, object] | None]:
        if data == "[DONE]":
            return None, "done", None
        try:
            body = json.loads(
                data, object_pairs_hook=_strict_object, parse_constant=_reject_constant
            )
        except json.JSONDecodeError as exc:
            raise GuardrailError("malformed SSE JSON") from exc
        if not isinstance(body, dict) or set(body) - {
            "id",
            "object",
            "created",
            "model",
            "choices",
        }:
            raise GuardrailError("unsupported completion shape")
        for key in ("id", "object", "model"):
            if key in body and not isinstance(body[key], str):
                raise GuardrailError("invalid completion metadata")
        if "created" in body and (
            not isinstance(body["created"], int) or isinstance(body["created"], bool)
        ):
            raise GuardrailError("invalid completion metadata")
        choices = body.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
        ):
            raise GuardrailError("exactly one choice required")
        choice = choices[0]
        if set(choice) - {"index", "delta", "finish_reason"}:
            raise GuardrailError("unsupported choice shape")
        if (
            "index" in choice
            and (
                not isinstance(choice["index"], int)
                or isinstance(choice["index"], bool)
            )
        ) or ("index" in choice and choice["index"] != 0):
            raise GuardrailError("choice index must be zero")
        delta = choice.get("delta", {})
        if not isinstance(delta, dict) or set(delta) - {"content", "role"}:
            raise GuardrailError("unsupported delta shape")
        if "role" in delta and delta["role"] != "assistant":
            raise GuardrailError("unsupported delta role")
        content = delta.get("content")
        if content is not None and not isinstance(content, str):
            raise GuardrailError("delta content must be text")
        finish = choice.get("finish_reason")
        if finish is not None and not isinstance(finish, str):
            raise GuardrailError("finish metadata must be text")
        metadata: dict[str, object] | None = (
            {"finish_reason": finish} if finish is not None else None
        )
        return content, None, metadata


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GuardrailError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise GuardrailError("non-finite JSON number")


def safe_sse(
    content: str | None = None, finish_reason: str | None = None, done: bool = False
) -> str:
    if done:
        return "data: [DONE]\n\n"
    choice: dict[str, object] = {"delta": {}}
    if content is not None:
        choice["delta"] = {"content": content}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return (
        "data: "
        + json.dumps({"choices": [choice]}, separators=(",", ":"), ensure_ascii=False)
        + "\n\n"
    )


class StreamingGuardrailCore:
    """Compose strict decoding, framing, parsing, redaction, and serialization."""

    __slots__ = (
        "_decoder",
        "_done",
        "_failed",
        "_finish_seen",
        "_finished",
        "_framer",
        "_parser",
        "_redactor",
    )

    def __init__(self) -> None:
        self._decoder = StrictUTF8Decoder()
        self._done = False
        self._finish_seen = False
        self._framer = SSEEventFramer()
        self._parser = ChatCompletionsDeltaParser()
        self._redactor = BoundedPIIRedactor()
        self._failed = False
        self._finished = False

    @property
    def pending_bytes(self) -> int:
        return self._framer.pending_bytes

    @property
    def pending_candidate_length(self) -> int:
        return self._redactor.pending_length

    @property
    def failed(self) -> bool:
        return self._failed

    def feed(self, fragment: bytes) -> list[str]:
        if self._failed or self._finished:
            return []
        try:
            text = self._decoder.feed(fragment)
            output: list[str] = []
            for event in self._framer.feed(text):
                content, marker, metadata = self._parser.parse(event)
                if marker == "done":
                    if not self._done:
                        output.extend(
                            safe_sse(part) for part in self._redactor.finish()
                        )
                        output.append(safe_sse(done=True))
                    self._done = True
                    continue
                if self._done or self._finish_seen:
                    raise GuardrailError("content after terminal marker")
                if content:
                    output.extend(
                        safe_sse(part) for part in self._redactor.feed(content)
                    )
                if metadata is not None:
                    for part in self._redactor.finish():
                        output.append(safe_sse(part))
                    finish_reason = metadata["finish_reason"]
                    if not isinstance(finish_reason, str):
                        raise GuardrailError("finish metadata must be text")
                    output.append(safe_sse(finish_reason=finish_reason))
                    self._finish_seen = True
            return output
        except GuardrailError:
            self._failed = True
            self._redactor.finish(normal=False)
            return []

    def finish(self, normal: bool = True) -> list[str]:
        if not normal or self._failed or self._finished:
            self._failed = True
            self._redactor.finish(normal=False)
            return []
        try:
            tail = self._decoder.finish()
            events = self._framer.feed(tail)
            if events:
                raise GuardrailError("unfinished terminal event")
            self._framer.finish()
            if not self._done:
                self._redactor.finish(normal=False)
                self._failed = True
                return []
            output = [safe_sse(part) for part in self._redactor.finish()]
            self._finished = True
            return output
        except GuardrailError:
            self._failed = True
            self._redactor.finish(normal=False)
            return []


def stream_core(fragments: Iterable[bytes]) -> list[str]:
    core = StreamingGuardrailCore()
    output = [part for fragment in fragments for part in core.feed(fragment)]
    output.extend(core.finish())
    return output
