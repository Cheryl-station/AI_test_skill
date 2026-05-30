import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def test_import_api_test_kit():
    module = importlib.import_module("api_test_kit")
    assert hasattr(module, "main")


def test_build_chinese_report_contains_summary():
    module = importlib.import_module("api_test_kit")
    summary = {
        "generated_at": "2026-05-30T00:00:00",
        "pytest_returncode": 0,
        "detected_endpoints": [{
            "methods": ["GET"],
            "path": "/health",
            "kind": "backend",
            "source": "samples/backend/app.py",
        }],
        "generated_tests": [str(Path("artifacts/generated-tests/test_api.py"))],
    }
    cases = [{
        "name": "test_health_get_smoke",
        "classname": "test_api",
        "time": "0.01",
        "status": "通过",
        "message": "",
    }]

    report = module.build_chinese_report(summary, cases)

    assert "接口测试报告" in report
    assert "整体结果" in report
    assert "通过" in report
    assert "成功率" in report
    assert "运行日志" in report
    assert "/health" in report


def test_parse_common_backend_framework_routes():
    module = importlib.import_module("api_test_kit")

    python_code = '''
@app.get("/health")
def health():
    pass

@api_router.post("/items")
def create_item():
    pass

urlpatterns = [
    path("users/", users_view),
]
'''
    js_code = '''
const express = require("express")
const app = express()
app.get("/api/users", handler)
router.post("/api/users", handler)
'''
    java_code = '''
@GetMapping("/orders")
public List<Order> orders() { return List.of(); }

@RequestMapping(value = "/orders", method = RequestMethod.POST)
public Order create() { return new Order(); }
'''

    endpoints = (
        module.parse_python_backend_endpoints("app.py", python_code)
        + module.parse_javascript_backend_endpoints("server.js", js_code)
        + module.parse_spring_endpoints("OrderController.java", java_code)
    )
    pairs = {(endpoint["methods"][0], endpoint["path"], endpoint["kind"]) for endpoint in endpoints}

    assert ("GET", "/health", "python-route") in pairs
    assert ("POST", "/items", "python-route") in pairs
    assert ("GET", "/users/", "django-route") in pairs
    assert ("GET", "/api/users", "express-route") in pairs
    assert ("POST", "/api/users", "express-route") in pairs
    assert ("GET", "/orders", "spring-route") in pairs
    assert ("POST", "/orders", "spring-route") in pairs


def test_detect_project_inputs_falls_back_to_project_root(tmp_path):
    module = importlib.import_module("api_test_kit")
    (tmp_path / "app.py").write_text('@app.get("/health")\n', encoding="utf-8")

    detected = module.detect_project_inputs(tmp_path)

    assert detected["frontend_paths"] == [tmp_path]
    assert detected["backend_paths"] == [tmp_path]
