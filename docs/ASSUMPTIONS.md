# Assumptions and limitations

These describe the verified assessment implementation, not general production guidance.

- The four tasks are independent components. Their packages do not form a runtime chain and do not share application state.
- Provider and downstream behavior uses deterministic local mocks and synthetic data. No paid provider, customer system, real credential, or undocumented `.env` is needed.
- Task 1 is a low-level MCP stdio boundary with exactly two tools; invalid arguments map to `-32602`, while valid business decline remains `isError=true`.
- Task 2 uses process-local synthetic credentials and fixed HS256 signing. Its `iat` claim is required metadata, not replay protection; production identity, rotation, and storage are out of scope.
- Task 3 is a narrow Chat-Completions-style SSE subset, not universal OpenAI compatibility. It supports one text choice and bounded conservative redaction.
- Task 4 uses SQLite WAL and `BEGIN IMMEDIATE`. WAL improves reader/writer coexistence but writers still serialize. The secondary 3000 ms cap is a project safety choice.
- Task 4 persists only HMAC-SHA256 tenant fingerprints; raw keys and fingerprint secrets remain configuration material.
- Thread 05's final verdict is `ACCEPT`, based on integrated evidence commit `a4e3e5c781fec7d7eaed779bbe9b2f0ab4b1affa`, with 176 passing tests and all demos successful.

## Platform constraint

`mcp==2.1.1` brings the `PyJWT[crypto]` path. Intel macOS selects the compatible `cryptography==48.0.1` universal2 wheel; other supported platforms select `cryptography==50.0.1`. Task 2 uses fixed symmetric HS256 only. No asymmetric JWT, MCP OAuth client credentials, or PKCS7 decryption is implemented. This is a narrow platform constraint, not general MCP guidance. Thread 05 reviewed it during security QA and returned `ACCEPT`.
