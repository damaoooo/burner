import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

import main  # noqa: E402


def schema():
    main.app.openapi_schema = None
    return main.app.openapi()


def test_frontend_routes_are_not_in_the_api_docs():
    paths = schema()["paths"]

    assert "/" not in paths
    assert "/{path}" not in paths
    assert all(path.startswith("/api/") for path in paths)


def test_every_endpoint_is_in_one_declared_group():
    doc = schema()
    declared = {tag["name"] for tag in doc["tags"]}

    for path, operations in doc["paths"].items():
        for method, operation in operations.items():
            assert len(operation.get("tags", [])) == 1, f"{method.upper()} {path}"
            assert operation["tags"][0] in declared, f"{method.upper()} {path}"
    assert {"Burn", "Power monitor", "Waveforms & profiles"} <= declared


def test_newer_endpoints_and_fields_are_described():
    doc = schema()
    paths, models = doc["paths"], doc["components"]["schemas"]

    for path, method in [
        ("/api/power/start", "post"),
        ("/api/power/history", "get"),
        ("/api/power/export.csv", "get"),
        ("/api/profiles/import", "post"),
        ("/api/waveform-assignments", "put"),
        ("/api/burn/start", "post"),
    ]:
        assert paths[path][method].get("description"), f"{method.upper()} {path}"
    assert models["BurnStartPayload"]["properties"]["cpu_engine"].get("description")
    assert models["PowerStartPayload"]["properties"]["interval_ms"].get("description")
    assert "/ws" in doc["info"]["description"]
