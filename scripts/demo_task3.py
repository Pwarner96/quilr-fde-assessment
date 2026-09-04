"""Run the pure Task 3 email-redaction spike without a live provider."""

from fde_assessment.task3_stream_guardrail.core import stream_core


def main() -> None:
    event = b'data: {"choices":[{"delta":{"content":"Contact paul@exa"}}]}\r'
    event += b'\n\ndata: {"choices":[{"delta":{"content":"mple.com now"},"finish_reason":"stop"}]}\n\n'
    event += b"data: [DONE]\n\n"
    chunks = [event[:17], event[17:31], event[31:]]
    print("".join(stream_core(chunks)))


if __name__ == "__main__":
    main()
