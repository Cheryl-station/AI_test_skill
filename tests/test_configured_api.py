import importlib
import json
import subprocess
import sys
from pathlib import Path


def kit():
    return importlib.import_module("api_test_kit")


def test_yaml_config_load_merge_and_env(monkeypatch, tmp_path):
    module = kit()
    monkeypatch.setenv("API_TOKEN", "secret-token-value")
    config_file = tmp_path / "case_config.yaml"
    config_file.write_text(
        """
version: 1
defaults:
  timeout: 7
  expected_status: [200]
endpoints:
  "GET /users/{user_id}":
    headers:
      Authorization: "Bearer ${ENV:API_TOKEN}"
    path_params:
      user_id: 12
    query:
      active: true
""",
        encoding="utf-8",
    )

    config = module.load_case_config(config_file)
    endpoint = {"methods": ["GET"], "path": "/users/{user_id}", "kind": "openapi", "source": "openapi.yaml"}
    case = module.build_request_case(endpoint, "http://api.test", config)

    assert case["timeout"] == 7
    assert case["expected_status"] == [200]
    assert case["url"] == "http://api.test/users/12"
    assert case["request"]["query"] == {"active": True}
    assert case["request"]["headers"]["Authorization"] == "Bearer secret-token-value"


def test_missing_env_marks_case_skipped(tmp_path):
    module = kit()
    config_file = tmp_path / "case_config.yaml"
    config_file.write_text(
        """
version: 1
endpoints:
  "GET /secure":
    headers:
      Authorization: "Bearer ${ENV:API_TOKEN_DOES_NOT_EXIST}"
""",
        encoding="utf-8",
    )

    config = module.load_case_config(config_file)
    case = module.build_request_case({"methods": ["GET"], "path": "/secure"}, "http://api.test", config)

    assert "API_TOKEN_DOES_NOT_EXIST" in case["skip_reason"]


def test_sensitive_values_are_redacted():
    module = kit()
    redacted = module.redact_sensitive({
        "Authorization": "Bearer abcdefghijklmnop",
        "nested": {"password": "super-secret-password"},
        "safe": "visible",
    })

    assert redacted["Authorization"] != "Bearer abcdefghijklmnop"
    assert "abcdef" not in redacted["Authorization"]
    assert redacted["nested"]["password"] != "super-secret-password"
    assert redacted["safe"] == "visible"


def test_path_params_replacement_and_missing_detection():
    module = kit()

    rendered, missing = module.apply_path_params("/users/{user_id}/notes/:note_id", {"user_id": 1})

    assert rendered == "/users/1/notes/:note_id"
    assert missing == ["note_id"]


def test_request_headers_query_json_and_form_generation():
    module = kit()
    openapi_docs = {
        "openapi.yaml": {
            "paths": {
                "/users/{user_id}": {
                    "parameters": [{"name": "user_id", "in": "path", "schema": {"type": "integer"}}],
                    "post": {
                        "parameters": [
                            {"name": "trace", "in": "header", "schema": {"default": "t1"}},
                            {"name": "verbose", "in": "query", "schema": {"enum": ["yes", "no"]}},
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"example": "Ada"}, "age": {"type": "integer"}},
                                    }
                                }
                            }
                        },
                    },
                }
            }
        }
    }
    endpoint = module.extract_openapi_endpoints(openapi_docs)[0]
    config = {
        "version": 1,
        "defaults": {},
        "missing_env": [],
        "endpoints": {"POST /users/{user_id}": {"allow_mutating": True, "form": {"ignored": "by json config override"}}},
    }

    case = module.build_request_case(endpoint, "http://api.test", config, allow_mutating_methods=True)

    assert case["url"] == "http://api.test/users/1"
    assert case["request"]["headers"] == {"trace": "t1"}
    assert case["request"]["query"] == {"verbose": "yes"}
    assert case["request"]["json"] == {"name": "Ada", "age": 1}
    assert case["skip_reason"] == ""


def test_status_strict_when_configured():
    module = kit()
    config = {
        "version": 1,
        "defaults": {},
        "missing_env": [],
        "endpoints": {"GET /health": {"expected_status": [204]}},
    }

    configured = module.build_request_case({"methods": ["GET"], "path": "/health"}, "http://api.test", config)
    legacy = module.build_request_case({"methods": ["GET"], "path": "/health"}, "http://api.test", None)

    assert configured["expected_status"] == [204]
    assert 400 not in configured["expected_status"]
    assert 400 in legacy["expected_status"]


def test_json_path_and_business_assertions():
    module = kit()
    payload = {"code": 0, "data": {"items": [{"id": "u1", "tags": ["a"]}], "name": "Alice"}}

    assert module.get_json_path(payload, "data.items[0].id") == (True, "u1")
    results = module.run_business_assertions(payload, [
        {"path": "code", "op": "eq", "value": 0},
        {"path": "data.name", "op": "regex", "value": "^Ali"},
        {"path": "data.items", "op": "length", "value": 1},
        {"path": "data.items[0].tags", "op": "contains", "value": "a"},
        {"path": "data.missing", "op": "not_exists"},
    ])
    assert all(result["passed"] for result in results)


def test_business_assertion_failure_includes_path_expected_actual():
    module = kit()
    try:
        module.run_business_assertions({"code": 1}, [{"path": "code", "op": "eq", "value": 0}])
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected assertion failure")

    assert "path=code" in message
    assert "expected=0" in message
    assert "actual=1" in message


def test_mutating_requests_skip_unless_both_authorized():
    module = kit()
    endpoint = {"methods": ["POST"], "path": "/users"}
    config = {"version": 1, "defaults": {}, "missing_env": [], "endpoints": {"POST /users": {"allow_mutating": True}}}
    missing_cli = module.build_request_case(endpoint, "http://api.test", config, allow_mutating_methods=False)
    missing_config = module.build_request_case(endpoint, "http://api.test", None, allow_mutating_methods=True)
    allowed = module.build_request_case(endpoint, "http://api.test", config, allow_mutating_methods=True)

    assert "--allow-mutating-methods" in missing_cli["skip_reason"]
    assert "allow_mutating" in missing_config["skip_reason"]
    assert allowed["skip_reason"] == ""


def test_openapi_example_default_enum_and_type_priority():
    module = kit()
    endpoint = {
        "methods": ["GET"],
        "path": "/items/{item_id}",
        "spec": {
            "parameters": [
                {"name": "item_id", "in": "path", "schema": {"example": 9, "default": 1}},
                {"name": "mode", "in": "query", "schema": {"default": "full", "enum": ["short"]}},
                {"name": "kind", "in": "query", "schema": {"enum": ["book"]}},
                {"name": "count", "in": "query", "schema": {"type": "integer"}},
            ]
        },
    }

    case = module.build_request_case(endpoint, "http://api.test", None)

    assert case["url"] == "http://api.test/items/9"
    assert case["request"]["query"] == {"mode": "full", "kind": "book", "count": 1}


def test_report_truncates_and_redacts_response():
    module = kit()
    text = module.truncate_and_redact(json.dumps({"token": "abcdef1234567890", "data": "x" * 3000}), 200)

    assert len(text) < 230
    assert "abcdef1234567890" not in text
    assert "[truncated]" in text


def test_sync_script_check_passes_after_sync():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "tools/sync_skill_script.py"], cwd=root, check=True, capture_output=True, text=True)
    result = subprocess.run([sys.executable, "tools/sync_skill_script.py", "--check"], cwd=root, capture_output=True, text=True)

    assert result.returncode == 0


def test_original_dry_run_cli_compatibility(tmp_path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text('@app.get("/health")\ndef health():\n    pass\n', encoding="utf-8")
    summary = tmp_path / "summary.json"

    result = subprocess.run(
        [sys.executable, str(root / "api_test_kit.py"), "--project-root", str(project), "--summary-file", str(summary), "--dry-run"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert summary.exists()
    assert "detected_endpoints" in json.loads(summary.read_text(encoding="utf-8"))
