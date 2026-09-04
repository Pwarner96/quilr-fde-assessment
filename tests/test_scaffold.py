import re
from importlib import import_module

from fde_assessment.common.logging import configure_safe_logger
from fde_assessment.common.request_id import generate_request_id, is_valid_request_id


def test_packages_import() -> None:
    import fde_assessment
    import fde_assessment.task1_mcp_server
    import fde_assessment.task2_mcp_gateway
    import fde_assessment.task3_stream_guardrail
    import fde_assessment.task4_model_router

    assert fde_assessment.__name__ == "fde_assessment"


def test_logger_binds_stderr(caplog, capsys) -> None:
    logger = configure_safe_logger("scaffold-test")
    logger.info("foundation event")
    captured = capsys.readouterr()
    assert "foundation event" in captured.err
    assert captured.out == ""
    logger.handlers.clear()


def test_request_ids_and_boundaries() -> None:
    assert re.fullmatch(r"req_[0-9a-f]{32}", generate_request_id())
    for value in ("a", "A_2:-.", "x" * 128):
        assert is_valid_request_id(value)
    for value in ("", "x" * 129, "has space", "\n", "é"):
        assert not is_valid_request_id(value)


def test_common_has_no_task_behavior() -> None:
    common = import_module("fde_assessment.common")

    public = {name for name in vars(common) if not name.startswith("_")}
    assert public <= {"logging", "request_id"}
