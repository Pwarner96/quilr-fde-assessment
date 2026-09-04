"""Bounded, incremental redaction of email-shaped text."""

import re

REDACTION = "[REDACTED]"
MAX_EMAIL_CHARS = 320

_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_DELIMITERS = frozenset(' \t\r\n,;:!?()[]{}<>"\\')


class EmailRedactor:
    """Redact complete email candidates without accumulating the response."""

    __slots__ = ("_draining", "_pending", "_replacement_emitted")

    def __init__(self) -> None:
        self._pending = ""
        self._draining = False
        self._replacement_emitted = False

    @property
    def pending_length(self) -> int:
        """Return pending candidate/token size without exposing its contents."""

        return len(self._pending)

    @property
    def draining(self) -> bool:
        return self._draining

    def feed(self, text_fragment: str) -> list[str]:
        """Consume one semantic fragment and return safe output fragments."""

        output: list[str] = []
        for character in text_fragment:
            if self._draining:
                if character in _DELIMITERS:
                    self._draining = False
                    self._replacement_emitted = False
                    output.append(character)
                continue

            if character in _DELIMITERS:
                output.extend(self._finish_pending())
                output.append(character)
                continue

            self._pending += character
            if len(self._pending) > MAX_EMAIL_CHARS:
                output.append(REDACTION)
                self._pending = ""
                self._draining = True
                self._replacement_emitted = True
        return output

    def finish(self, normal: bool = True) -> list[str]:
        """Resolve pending safe text, or discard all unresolved state."""

        if not normal:
            self._clear()
            return []
        if self._draining:
            self._clear()
            return []
        output = self._finish_pending()
        self._clear()
        return output

    def _finish_pending(self) -> list[str]:
        if not self._pending:
            return []
        pending = self._pending
        self._pending = ""
        if "@" in pending and _EMAIL.fullmatch(pending) is not None:
            return [REDACTION]
        return [pending]

    def _clear(self) -> None:
        self._pending = ""
        self._draining = False
        self._replacement_emitted = False
