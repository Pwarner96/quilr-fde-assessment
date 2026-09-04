# Core 4B state and ownership

```text
request
  -> strict contract + deterministic token count
  -> SQLite BEGIN IMMEDIATE reservation commit
  -> primary attempt (router owns deadline and response ownership)
       -> 200: normalize primary result, reconcile downward
       -> 429: cleanup/secondary once
       -> deadline: cancel + await cleanup, then secondary once
       -> other failure or caller cancellation: no fallback
  -> secondary attempt (independent 3000 ms cap, no retry/tertiary)
  -> one typed RouterResult owned by router
```

The core uses logical provider roles only; provider destinations, HTTP client
lifespan, public error envelopes, and FastAPI assembly remain later work.
Timeout tests use controlled events and bounded async timeouts; exact wall-clock
scheduling at 3000 ms is not claimed.
