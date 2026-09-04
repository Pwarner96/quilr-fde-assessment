"""Run the pure Task 3 email-redaction spike without a live provider."""

from fde_assessment.task3_stream_guardrail.email_redactor import EmailRedactor


def main() -> None:
    redactor = EmailRedactor()
    output = redactor.feed("Contact paul@exa")
    output.extend(redactor.feed("mple.com now"))
    output.extend(redactor.finish())
    print("".join(output))


if __name__ == "__main__":
    main()
