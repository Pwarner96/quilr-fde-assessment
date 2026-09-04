import re
import uuid

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)


def is_valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID.fullmatch(value))


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"
