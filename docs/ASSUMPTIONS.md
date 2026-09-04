# Foundation assumptions

- Exactly four implementation tasks are planned.
- The project uses Python 3.12 in one monorepo.
- Mock-first execution and synthetic data are the defaults.
- There is no Task 5.
- The scaffold completes no task implementation.
- `mcp==2.1.1` requests `PyJWT[crypto]`. Intel macOS requires the compatible `cryptography==48.0.1` universal2 wheel; other supported platforms select `cryptography==50.0.1`. Task 2 uses fixed symmetric `HS256` only. No asymmetric JWT, MCP OAuth client credentials, or PKCS7 decryption is implemented. This is a narrow platform-compatibility exception, not general MCP guidance, and Thread 05 must review it during security QA.
