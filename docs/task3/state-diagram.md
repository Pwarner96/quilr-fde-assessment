# Task 3 Core State Diagram

```text
BYTES -> strict UTF-8 decoder -> SSE lines/events -> one-choice delta parser
                                      | malformed/over-bound -> FAILED
                                      v
                         bounded PII candidate scanner
                         | delimiter -> SAFE/REDACTED output
                         | overflow -> REDACTED + DRAIN
                                      v
                         canonical SSE serializer -> downstream chunks
```

The core supports only one text choice, `delta.content` strings, optional string
`finish_reason`, and `[DONE]`. Email syntax is the ASCII mailbox/domain subset
implemented by the regex; SSNs are plain 9 digits or `###-##-####`; cards are
13–19 digits with optional hyphens and a valid Luhn checksum. Delimiters
are conservative ASCII punctuation/whitespace. Unsupported provider fields,
malformed JSON, incomplete UTF-8/events, and over-bound events fail closed.
