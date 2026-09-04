"""Small local demonstration of limiter-only admission (no provider execution)."""

import tempfile
from pathlib import Path

from fde_assessment.task4_model_router import AdmissionStatus, RateLimiter

with tempfile.TemporaryDirectory() as directory:
    limiter = RateLimiter(
        Path(directory) / "demo.sqlite", quota_limit=10, fingerprint_secret=b"d" * 32
    )
    limiter.initialize()
    result = limiter.admit(
        "demo-tenant", "demo-request", estimated_input_tokens=2, max_output_tokens=3
    )
    print(f"status={result.status} charged={limiter.active_charge('demo-tenant')}")
    assert result.status is AdmissionStatus.ADMITTED
