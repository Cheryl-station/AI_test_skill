#!/usr/bin/env python3
"""Generate and run lightweight API smoke tests from source files."""

import argparse
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

try:
    import yaml
except ImportError:
    yaml = None

FRONTEND_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".vue"}
BACKEND_EXTENSIONS = {".py", ".java", ".kt", ".go", ".rb", ".cs", ".js", ".jsx", ".ts", ".tsx"}
OPENAPI_EXTENSIONS = {".json", ".yaml", ".yml"}
IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "artifacts",
    ".next",
    ".nuxt",
}
FRONTEND_DIR_HINTS = ("frontend", "client", "web", "ui", "src")
BACKEND_DIR_HINTS = ("backend", "server", "api", "app", "src")
DEFAULT_OUTPUT_DIR = Path("artifacts/generated-tests")
DEFAULT_REPORT_FILE = Path("artifacts/junit.xml")
DEFAULT_SUMMARY_FILE = Path("artifacts/summary.json")
DEFAULT_HTML_REPORT_FILE = Path("artifacts/report.html")
API_DEFAULT_BASE_URL = "http://localhost:8000"
SERVICE_STARTUP_POLL_INTERVAL = 0.5
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LEGACY_EXPECTED_STATUS = [200, 201, 204, 400, 404]
SUPPORTED_ASSERTION_OPS = {"eq", "ne", "exists", "not_exists", "not_empty", "contains", "in", "type", "regex", "length"}
SENSITIVE_KEY_PATTERN = re.compile(r"(authorization|cookie|token|password|secret|api[-_]?key)", re.IGNORECASE)
ENV_PATTERN = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")
PYTHON_DEPENDENCIES = {
    "requests": "requests",
    "pytest": "pytest",
    "yaml": "PyYAML",
}


def check_runtime_dependencies() -> List[str]:
    missing = []
    for module_name, package_name in PYTHON_DEPENDENCIES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return missing


def is_ignored_path(path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in path.parts)


def collect_source(paths: List[Path], extensions: set) -> Dict[str, str]:
    files = {}
    for path in paths:
        if path.is_file() and path.suffix in extensions:
            files[str(path)] = path.read_text(encoding="utf-8")
        elif path.is_dir():
            for child in path.rglob("*"):
                if is_ignored_path(child.relative_to(path)):
                    continue
                if child.is_file() and child.suffix in extensions:
                    files[str(child)] = child.read_text(encoding="utf-8")
    return files


def detect_project_inputs(project_root: Path) -> Dict[str, List[Path]]:
    frontend_paths = find_hint_directories(project_root, FRONTEND_DIR_HINTS)
    backend_paths = find_hint_directories(project_root, BACKEND_DIR_HINTS)
    openapi_paths = []

    for child in project_root.rglob("*"):
        if is_ignored_path(child.relative_to(project_root)):
            continue
        if child.is_file() and child.suffix.lower() in OPENAPI_EXTENSIONS:
            lowered = child.name.lower()
            if any(token in lowered for token in ("openapi", "swagger", "api-doc")):
                openapi_paths.append(child)

    if not frontend_paths and not backend_paths and not openapi_paths:
        frontend_paths = [project_root]
        backend_paths = [project_root]

    return {
        "frontend_paths": dedupe_paths(frontend_paths),
        "backend_paths": dedupe_paths(backend_paths),
        "openapi_paths": dedupe_paths(openapi_paths),
    }


def find_hint_directories(project_root: Path, hints: tuple) -> List[Path]:
    paths = []
    for child in project_root.rglob("*"):
        if is_ignored_path(child.relative_to(project_root)):
            continue
        if child.is_dir() and child.name.lower() in hints:
            paths.append(child)
    direct_matches = [path for path in paths if path.parent == project_root]
    return direct_matches or paths


def dedupe_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    unique = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def load_openapi_documents(paths: List[Path]) -> Dict[str, dict]:
    openapi_docs = {}
    for path in paths:
        candidates = [path] if path.is_file() else path.rglob("*")
        for child in candidates:
            if path.is_dir() and is_ignored_path(child.relative_to(path)):
                continue
            if not child.is_file() or child.suffix.lower() not in OPENAPI_EXTENSIONS:
                continue
            try:
                text = child.read_text(encoding="utf-8")
                if child.suffix.lower() == ".json":
                    openapi_docs[str(child)] = json.loads(text)
                elif yaml is not None:
                    openapi_docs[str(child)] = yaml.safe_load(text)
            except (OSError, json.JSONDecodeError):
                continue
            except yaml.YAMLError if yaml is not None else Exception:
                continue
    return openapi_docs


def resolve_env_vars(value: Any, missing: Optional[List[str]] = None) -> Any:
    """Resolve ${ENV:NAME} placeholders without evaluating arbitrary code."""
    if missing is None:
        missing = []
    if isinstance(value, dict):
        return {key: resolve_env_vars(item, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(item, missing) for item in value]
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            name = match.group(1)
            env_value = os.environ.get(name)
            if env_value is None:
                missing.append(name)
                return match.group(0)
            return env_value
        return ENV_PATTERN.sub(replace, value)
    return value


def load_case_config(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {"version": 1, "defaults": {}, "endpoints": {}, "missing_env": []}
    if yaml is None:
        raise RuntimeError("缺少 PyYAML 依赖，无法读取 --case-config。")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("case_config.yaml 顶层必须是对象。")
    missing: List[str] = []
    resolved = resolve_env_vars(raw, missing)
    resolved.setdefault("version", 1)
    resolved.setdefault("defaults", {})
    resolved.setdefault("endpoints", {})
    if not isinstance(resolved["defaults"], dict) or not isinstance(resolved["endpoints"], dict):
        raise ValueError("case_config.yaml 的 defaults 和 endpoints 必须是对象。")
    resolved["missing_env"] = sorted(set(missing))
    return resolved


def endpoint_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def parse_endpoint_key(key: str) -> Tuple[str, str]:
    method, _, path = key.strip().partition(" ")
    return method.upper(), path.strip()


def get_endpoint_config(case_config: Optional[Dict[str, Any]], method: str, path: str) -> Dict[str, Any]:
    if not case_config:
        return {}
    endpoint_config = case_config.get("endpoints", {}).get(endpoint_key(method, path), {})
    if endpoint_config is None:
        endpoint_config = {}
    if not isinstance(endpoint_config, dict):
        raise ValueError(f"接口配置必须是对象：{endpoint_key(method, path)}")
    defaults = case_config.get("defaults", {}) or {}
    merged = {**defaults, **endpoint_config}
    if endpoint_config:
        merged["_configured"] = True
    if case_config.get("missing_env"):
        merged["_missing_env"] = case_config["missing_env"]
    return merged


def redact_sensitive(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                redacted[key] = redact_scalar(item)
            else:
                redacted[key] = redact_sensitive(item, str(key))
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item, parent_key) for item in value]
    if SENSITIVE_KEY_PATTERN.search(parent_key):
        return redact_scalar(value)
    return value


def redact_scalar(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-2:]}"


def truncate_and_redact(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(redact_sensitive(value), ensure_ascii=False)
    if text[:1] in "[{":
        try:
            text = json.dumps(redact_sensitive(json.loads(text)), ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def extract_path_param_names(path: str) -> List[str]:
    names = re.findall(r"\{([^}/]+)\}", path)
    names.extend(re.findall(r"<(?:[^:<>]+:)?([^<>]+)>", path))
    names.extend(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", path))
    return sorted(set(names))


def apply_path_params(path: str, path_params: Dict[str, Any]) -> Tuple[str, List[str]]:
    missing = []
    rendered = path
    for name in extract_path_param_names(path):
        if name not in path_params or path_params[name] in (None, ""):
            missing.append(name)
            continue
        value = str(path_params[name])
        rendered = rendered.replace("{" + name + "}", value)
        rendered = re.sub(rf"<(?:[^:<>]+:)?{re.escape(name)}>", value, rendered)
        rendered = re.sub(rf":{re.escape(name)}\b", value, rendered)
    return rendered, missing


def _path_tokens(path: str) -> List[Any]:
    tokens: List[Any] = []
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?", part)
        if not match:
            tokens.append(part)
            continue
        tokens.append(match.group(1))
        if match.group(2) is not None:
            tokens.append(int(match.group(2)))
    return tokens


def get_json_path(payload: Any, path: str) -> Tuple[bool, Any]:
    current = payload
    for token in _path_tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False, None
            current = current[token]
    return True, current


def assert_business(payload: Any, assertion: Dict[str, Any]) -> Dict[str, Any]:
    path = assertion.get("path", "")
    op = assertion.get("op")
    expected = assertion.get("value")
    exists, actual = get_json_path(payload, path)
    passed = False
    if op == "eq":
        passed = exists and actual == expected
    elif op == "ne":
        passed = (not exists) or actual != expected
    elif op == "exists":
        passed = exists
    elif op == "not_exists":
        passed = not exists
    elif op == "not_empty":
        passed = exists and actual not in (None, "", [], {})
    elif op == "contains":
        try:
            passed = exists and expected in actual
        except TypeError:
            passed = False
    elif op == "in":
        try:
            passed = exists and actual in expected
        except TypeError:
            passed = False
    elif op == "type":
        type_map = {"str": str, "string": str, "int": int, "integer": int, "float": float, "number": (int, float), "bool": bool, "boolean": bool, "list": list, "array": list, "dict": dict, "object": dict, "null": type(None)}
        passed = exists and isinstance(actual, type_map.get(str(expected).lower(), object))
    elif op == "regex":
        passed = exists and re.search(str(expected), str(actual)) is not None
    elif op == "length":
        passed = exists and hasattr(actual, "__len__") and len(actual) == expected
    else:
        raise AssertionError(f"不支持的断言操作：{op}")
    return {"path": path, "op": op, "expected": expected, "actual": actual if exists else "<missing>", "passed": passed}


def run_business_assertions(payload: Any, assertions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    for assertion in assertions or []:
        if assertion.get("op") not in SUPPORTED_ASSERTION_OPS:
            raise AssertionError(f"不支持的断言操作：{assertion.get('op')}")
        result = assert_business(payload, assertion)
        results.append(result)
        if not result["passed"]:
            raise AssertionError(
                f"业务断言失败 path={result['path']} op={result['op']} "
                f"expected={result['expected']!r} actual={result['actual']!r}"
            )
    return results


def schema_default_value(schema: Dict[str, Any]) -> Any:
    if not isinstance(schema, dict):
        return "example"
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    schema_type = schema.get("type")
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return [schema_default_value(schema.get("items", {"type": "string"}))]
    if schema_type == "object" or "properties" in schema:
        return {key: schema_default_value(value) for key, value in (schema.get("properties") or {}).items()}
    return "example"


def openapi_request_parts(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    spec = endpoint.get("spec") if isinstance(endpoint.get("spec"), dict) else {}
    parts: Dict[str, Any] = {"headers": {}, "query": {}, "path_params": {}, "json": None, "form": None}
    for parameter in spec.get("parameters", []) or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        location = parameter.get("in")
        if not name or location not in {"header", "query", "path"}:
            continue
        value = schema_default_value(parameter.get("schema", {}))
        if "example" in parameter:
            value = parameter["example"]
        target = {"header": "headers", "query": "query", "path": "path_params"}[location]
        parts[target][name] = value
    request_body = spec.get("requestBody", {}) if isinstance(spec.get("requestBody"), dict) else {}
    content = request_body.get("content", {}) if isinstance(request_body.get("content"), dict) else {}
    json_content = content.get("application/json")
    form_content = content.get("application/x-www-form-urlencoded") or content.get("multipart/form-data")
    if isinstance(json_content, dict):
        parts["json"] = json_content.get("example", schema_default_value(json_content.get("schema", {})))
    elif isinstance(form_content, dict):
        form_value = form_content.get("example", schema_default_value(form_content.get("schema", {})))
        parts["form"] = form_value if isinstance(form_value, dict) else {}
    return parts


def build_request_case(
    endpoint: Dict[str, Any],
    api_base_url: str = API_DEFAULT_BASE_URL,
    case_config: Optional[Dict[str, Any]] = None,
    allow_mutating_methods: bool = False,
) -> Dict[str, Any]:
    method = endpoint["methods"][0].upper()
    raw_path = str(endpoint["path"])
    config = get_endpoint_config(case_config, method, raw_path)
    configured = bool(config.get("_configured"))
    openapi_parts = openapi_request_parts(endpoint)
    path_params = {**openapi_parts["path_params"], **(config.get("path_params") or {})}
    if not path_params and not configured:
        path_params = {name: 1 for name in extract_path_param_names(raw_path)}
    rendered_path, missing_path_params = apply_path_params(raw_path, path_params)
    request_data = {
        "headers": {**openapi_parts["headers"], **(config.get("headers") or {})},
        "query": {**openapi_parts["query"], **(config.get("query") or {})},
        "json": config.get("json", openapi_parts.get("json")),
        "form": config.get("form", openapi_parts.get("form")),
    }
    expected_status = config.get("expected_status", LEGACY_EXPECTED_STATUS)
    if isinstance(expected_status, int):
        expected_status = [expected_status]
    skip_reason = ""
    if config.get("enabled") is False:
        skip_reason = "case_config enabled=false"
    elif config.get("_missing_env"):
        skip_reason = "环境变量不存在：" + ", ".join(config["_missing_env"])
    elif missing_path_params:
        skip_reason = "缺少必填 path_params：" + ", ".join(missing_path_params)
    elif method in MUTATING_METHODS and not allow_mutating_methods:
        skip_reason = "修改类请求默认跳过：缺少 --allow-mutating-methods"
    elif method in MUTATING_METHODS and not config.get("allow_mutating"):
        skip_reason = "修改类请求默认跳过：接口配置未设置 allow_mutating: true"
    return {
        "name": make_test_name(raw_path, method),
        "method": method,
        "path": raw_path,
        "url": api_base_url.rstrip("/") + rendered_path,
        "source": endpoint.get("source", ""),
        "source_kind": endpoint.get("kind", ""),
        "summary": endpoint.get("summary", ""),
        "timeout": config.get("timeout", 10),
        "expected_status": expected_status,
        "assertions": config.get("assertions", []),
        "configured": configured,
        "skip_reason": skip_reason,
        "request": request_data,
        "redacted_request": redact_sensitive(request_data),
    }


def extract_openapi_endpoints(openapi_docs: Dict[str, dict]) -> List[Dict[str, object]]:
    endpoints = []
    for path, document in openapi_docs.items():
        if not isinstance(document, dict):
            continue
        for route, methods in document.get("paths", {}).items():
            if not isinstance(methods, dict):
                continue
            for method, spec in methods.items():
                if method.upper() not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"}:
                    continue
                combined_spec = spec if isinstance(spec, dict) else {}
                path_parameters = methods.get("parameters", []) if isinstance(methods.get("parameters"), list) else []
                operation_parameters = combined_spec.get("parameters", []) if isinstance(combined_spec.get("parameters"), list) else []
                combined_spec = {**combined_spec, "parameters": path_parameters + operation_parameters}
                endpoints.append({
                    "source": path,
                    "path": route,
                    "methods": [method.upper()],
                    "kind": "openapi",
                    "summary": spec.get("summary", "") if isinstance(spec, dict) else "",
                    "spec": combined_spec,
                })
    return endpoints


def extract_api_endpoints(files: Dict[str, str], openapi_docs: Optional[Dict[str, dict]] = None) -> List[Dict[str, object]]:
    endpoints = []
    if openapi_docs:
        endpoints.extend(extract_openapi_endpoints(openapi_docs))
    for path, code in files.items():
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            endpoints.extend(parse_python_backend_endpoints(path, code))
        elif suffix in {".java", ".kt"}:
            endpoints.extend(parse_spring_endpoints(path, code))
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            endpoints.extend(parse_javascript_backend_endpoints(path, code))
            endpoints.extend(parse_frontend_api_calls(path, code))
        elif suffix in FRONTEND_EXTENSIONS:
            endpoints.extend(parse_frontend_api_calls(path, code))
    return dedupe_endpoints(endpoints)


def parse_python_backend_endpoints(path: str, code: str) -> List[Dict[str, object]]:
    endpoints = []
    route_pattern = re.compile(
        r"@\w*\.route\(\s*['\"](?P<path>[^'\"]+)['\"],\s*methods\s*=\s*\[(?P<methods>[^\]]+)\]",
        re.IGNORECASE,
    )
    method_pattern = re.compile(
        r"@\w*\.(?P<method>get|post|put|delete|patch)\(\s*['\"](?P<path>[^'\"]+)['\"]",
        re.IGNORECASE,
    )
    django_path_pattern = re.compile(
        r"(?:path|re_path)\(\s*['\"](?P<path>[^'\"]+)['\"]",
    )
    for match in route_pattern.finditer(code):
        methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", match.group("methods"), re.IGNORECASE)
        endpoints.append({
            "source": path,
            "path": match.group("path"),
            "methods": [method.upper() for method in methods] or ["GET"],
            "kind": "python-route",
        })
    for match in method_pattern.finditer(code):
        endpoints.append({
            "source": path,
            "path": match.group("path"),
            "methods": [match.group("method").upper()],
            "kind": "python-route",
        })
    for match in django_path_pattern.finditer(code):
        route = normalize_path("/" + match.group("path").lstrip("/"))
        endpoints.append({
            "source": path,
            "path": route,
            "methods": ["GET"],
            "kind": "django-route",
        })
    return endpoints


def parse_javascript_backend_endpoints(path: str, code: str) -> List[Dict[str, object]]:
    endpoints = []
    express_pattern = re.compile(
        r"\b(?:app|router)\.(?P<method>get|post|put|delete|patch)\(\s*['\"`](?P<path>[^'\"`]+)['\"`]",
        re.IGNORECASE,
    )
    for match in express_pattern.finditer(code):
        endpoints.append({
            "source": path,
            "path": normalize_path(match.group("path")),
            "methods": [match.group("method").upper()],
            "kind": "express-route",
        })
    return endpoints


def parse_spring_endpoints(path: str, code: str) -> List[Dict[str, object]]:
    endpoints = []
    shortcut_pattern = re.compile(
        r"@(?P<method>Get|Post|Put|Delete|Patch)Mapping\(\s*(?:value\s*=\s*)?['\"](?P<path>[^'\"]+)['\"]",
    )
    request_mapping_pattern = re.compile(
        r"@RequestMapping\((?P<body>[^)]*)\)",
        re.DOTALL,
    )
    for match in shortcut_pattern.finditer(code):
        endpoints.append({
            "source": path,
            "path": normalize_path(match.group("path")),
            "methods": [match.group("method").upper().replace("MAPPING", "")],
            "kind": "spring-route",
        })
    for match in request_mapping_pattern.finditer(code):
        body = match.group("body")
        path_match = re.search(r"(?:value|path)\s*=\s*['\"](?P<path>[^'\"]+)['\"]", body)
        method_match = re.search(r"RequestMethod\.(?P<method>GET|POST|PUT|DELETE|PATCH)", body)
        if path_match:
            endpoints.append({
                "source": path,
                "path": normalize_path(path_match.group("path")),
                "methods": [method_match.group("method") if method_match else "GET"],
                "kind": "spring-route",
            })
    return endpoints


def parse_frontend_api_calls(path: str, code: str) -> List[Dict[str, object]]:
    endpoints = []
    axios_pattern = re.compile(
        r"axios\.(?P<method>get|post|put|delete|patch)\(['\"](?P<path>[^'\"]+)['\"]",
        re.IGNORECASE,
    )
    fetch_pattern = re.compile(
        r"fetch\((?P<quote>['\"`])(?P<path>[^'\"`]+)(?P=quote)(?:\s*,\s*\{(?P<options>[^}]+)\})?",
        re.IGNORECASE | re.DOTALL,
    )
    for match in axios_pattern.finditer(code):
        endpoints.append({
            "source": path,
            "path": normalize_path(match.group("path")),
            "methods": [match.group("method").upper()],
            "kind": "frontend",
        })
    for match in fetch_pattern.finditer(code):
        options = match.group("options") or ""
        method_match = re.search(r"method\s*:\s*['\"](?P<method>GET|POST|PUT|DELETE|PATCH)['\"]", options, re.IGNORECASE)
        endpoints.append({
            "source": path,
            "path": normalize_path(match.group("path")),
            "methods": [method_match.group("method").upper() if method_match else "GET"],
            "kind": "frontend",
        })
    return endpoints


def normalize_path(path: str) -> str:
    match = re.search(r"(/[A-Za-z0-9_./{}:-]+)", path)
    return match.group(1) if match else path


def dedupe_endpoints(endpoints: List[Dict[str, object]]) -> List[Dict[str, object]]:
    seen = set()
    unique = []
    for endpoint in endpoints:
        for method in endpoint["methods"]:
            key = (method, endpoint["path"])
            if key in seen:
                continue
            seen.add(key)
            unique.append({**endpoint, "methods": [method]})
    return unique


def ensure_service_available(health_url: str, timeout: int = 30) -> bool:
    if requests is None:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(health_url, timeout=3)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(SERVICE_STARTUP_POLL_INTERVAL)
    return False


def start_service(command: str, cwd: Path, health_url: Optional[str], timeout: int = 30) -> subprocess.Popen:
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if health_url and not ensure_service_available(health_url, timeout):
        process.terminate()
        raise RuntimeError(f"Service did not become healthy within {timeout} seconds: {health_url}")
    return process


def make_test_name(path: str, method: str) -> str:
    name = path.strip("/\n").replace("/", "_").replace("{", "_").replace("}", "_") or "root"
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
    return f"{name}_{method.lower()}_smoke"


def python_literal(value: str) -> str:
    return repr(value)


def example_path(path: str) -> str:
    path = re.sub(r"\{[^}/]+\}", "1", path)
    path = re.sub(r"<(?:[^:<>]+:)?[^<>]+>", "1", path)
    path = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", "1", path)
    return path


def endpoints_with_case_config(endpoints: List[Dict[str, Any]], case_config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not case_config:
        return endpoints
    seen = {endpoint_key(endpoint["methods"][0], endpoint["path"]) for endpoint in endpoints}
    merged = list(endpoints)
    for key in case_config.get("endpoints", {}):
        method, path = parse_endpoint_key(key)
        if not method or not path or key in seen:
            continue
        merged.append({
            "source": "case_config",
            "path": path,
            "methods": [method],
            "kind": "case-config",
            "summary": "",
        })
        seen.add(key)
    return merged


def generated_test_code(cases: List[Dict[str, Any]], api_base_url: str) -> str:
    generated_cases = []
    for case in cases:
        generated_cases.append({
            "name": case["name"],
            "method": case["method"],
            "url": case["url"],
            "timeout": case["timeout"],
            "expected_status": case["expected_status"],
            "assertions": case["assertions"],
            "skip_reason": case["skip_reason"],
            "request": case["request"],
            "redacted_request": case["redacted_request"],
        })
    return (
        "import json\n"
        "import time\n"
        "import pytest\n"
        "import requests\n"
        "from api_test_kit import run_business_assertions, truncate_and_redact\n\n"
        f"BASE_URL = {python_literal(api_base_url)}\n"
        f"CASES = {repr(generated_cases)}\n\n"
        "@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])\n"
        "def test_api_endpoint(case):\n"
        "    if case.get('skip_reason'):\n"
        "        pytest.skip(case['skip_reason'])\n"
        "    request_kwargs = {\n"
        "        'headers': case['request'].get('headers') or None,\n"
        "        'params': case['request'].get('query') or None,\n"
        "        'timeout': case.get('timeout', 10),\n"
        "    }\n"
        "    if case['request'].get('json') is not None:\n"
        "        request_kwargs['json'] = case['request']['json']\n"
        "    if case['request'].get('form') is not None:\n"
        "        request_kwargs['data'] = case['request']['form']\n"
        "    started = time.perf_counter()\n"
        "    response = requests.request(case['method'], case['url'], **request_kwargs)\n"
        "    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)\n"
        "    response_text = truncate_and_redact(response.text, 2000)\n"
        "    assert response.status_code in case['expected_status'], (\n"
        "        f\"状态码断言失败 method={case['method']} url={case['url']} \"\n"
        "        f\"expected={case['expected_status']} actual={response.status_code} \"\n"
        "        f\"elapsed_ms={elapsed_ms} request={case['redacted_request']} response={response_text}\"\n"
        "    )\n"
        "    if case.get('assertions'):\n"
        "        try:\n"
        "            payload = response.json()\n"
        "        except ValueError as exc:\n"
        "            raise AssertionError(f\"响应不是 JSON，无法执行业务断言：{response_text}\") from exc\n"
        "        run_business_assertions(payload, case['assertions'])\n"
    )


def generate_tests(
    frontend: Dict[str, str],
    backend: Dict[str, str],
    mode: str = "api",
    api_base_url: str = API_DEFAULT_BASE_URL,
    openapi_docs: Optional[Dict[str, dict]] = None,
    data_driven: bool = False,
    case_config: Optional[Dict[str, Any]] = None,
    allow_mutating_methods: bool = False,
) -> List[Dict[str, str]]:
    if mode != "api":
        return [{
            "id": "project_placeholder",
            "title": "Project smoke test",
            "description": "Placeholder test for projects without detected API endpoints.",
            "code": "def test_project_placeholder():\n    assert True\n",
        }]

    endpoints = endpoints_with_case_config(extract_api_endpoints({**frontend, **backend}, openapi_docs), case_config)
    if not endpoints:
        return [{
            "id": "api_placeholder",
            "title": "API smoke test placeholder",
            "description": "No API endpoints were detected.",
            "code": "def test_api_placeholder():\n    assert True\n",
        }]

    if data_driven:
        cases = [build_request_case(endpoint, api_base_url, case_config, allow_mutating_methods) for endpoint in endpoints]
        entries = ",\n".join(
            "    "
            f"({python_literal(case['url'].replace(api_base_url.rstrip('/'), '', 1))}, "
            f"{python_literal(case['method'])}, "
            f"{repr(case['request'].get('json') or case['request'].get('form') or {})})"
            for case in cases
        )
        return [{
            "id": "api_smoke_matrix",
            "title": "API smoke test matrix",
            "description": "Parameterized smoke checks for detected endpoints.",
            "code": (
                "import pytest\n"
                "import requests\n\n"
                f"BASE_URL = {python_literal(api_base_url)}\n\n"
                "@pytest.mark.parametrize('path,method,payload', [\n"
                f"{entries}\n"
                "])\n"
                "def test_api_endpoint_smoke(path, method, payload):\n"
                "    if method in {'POST', 'PUT', 'PATCH', 'DELETE'}:\n"
                "        pytest.skip('修改类请求默认跳过：缺少 --allow-mutating-methods')\n"
                "    response = requests.request(method, f\"{BASE_URL}{path}\", json=payload if payload else None)\n"
                f"    assert response.status_code in {tuple(LEGACY_EXPECTED_STATUS)}\n"
            ),
        }]

    cases = [build_request_case(endpoint, api_base_url, case_config, allow_mutating_methods) for endpoint in endpoints]
    return [{
        "id": "api_configured_matrix",
        "title": "API configured test matrix",
        "description": "Configuration-aware API checks for detected endpoints.",
        "cases": cases,
        "code": generated_test_code(cases, api_base_url),
    }]


def default_payload(method: str) -> str:
    return "{'name': 'example', 'age': 1}" if method.upper() in {"POST", "PUT", "PATCH"} else "{}"


def write_tests(tests: List[Dict[str, str]], output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for test in tests:
        name = test.get("id", "api_test").replace(" ", "_").lower()
        filename = output_dir / f"test_{name}.py"
        header = f"# Generated by api-test-kit on {datetime.utcnow().isoformat()}\n\n"
        filename.write_text(header + (test.get("code") or "def test_placeholder():\n    assert True\n") + "\n", encoding="utf-8")
        written.append(filename)
    return written


def run_pytest(output_dir: Path, report_path: Path) -> Dict[str, object]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("pytest") is None:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": "缺少 pytest 依赖，请先安装：pip install pytest",
            "report_path": str(report_path),
        }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(output_dir), "--junitxml", str(report_path)],
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "report_path": str(report_path),
    }


def save_summary(summary: Dict[str, object], summary_path: Path) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _junit_status(testcase: ElementTree.Element) -> str:
    if testcase.find("failure") is not None:
        return "失败"
    if testcase.find("error") is not None:
        return "错误"
    if testcase.find("skipped") is not None:
        return "跳过"
    return "通过"


def _junit_message(testcase: ElementTree.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        node = testcase.find(tag)
        if node is not None:
            return node.get("message") or (node.text or "").strip()
    return ""


def load_junit_cases(report_path: Path) -> List[Dict[str, str]]:
    if not report_path.exists():
        return []
    root = ElementTree.parse(report_path).getroot()
    cases = []
    for testcase in root.iter("testcase"):
        cases.append({
            "name": testcase.get("name", ""),
            "classname": testcase.get("classname", ""),
            "time": testcase.get("time", "0"),
            "status": _junit_status(testcase),
            "message": _junit_message(testcase),
        })
    return cases


def build_chinese_report(summary: Dict[str, object], junit_cases: List[Dict[str, str]]) -> str:
    passed = sum(1 for case in junit_cases if case["status"] == "通过")
    failed = sum(1 for case in junit_cases if case["status"] == "失败")
    errors = sum(1 for case in junit_cases if case["status"] == "错误")
    skipped = sum(1 for case in junit_cases if case["status"] == "跳过")
    total = len(junit_cases)
    success_rate = f"{(passed / total * 100):.1f}%" if total else "0.0%"
    total_duration = sum(float(case.get("time") or 0) for case in junit_cases)
    endpoints = summary.get("detected_endpoints", [])
    request_cases = summary.get("request_cases", [])
    generated_tests = summary.get("generated_tests", [])
    returncode = summary.get("pytest_returncode", "未执行")
    generated_at = summary.get("generated_at", "")
    project_root = summary.get("project_root", "")
    pytest_stdout = summary.get("pytest_stdout", "")
    pytest_stderr = summary.get("pytest_stderr", "")
    failed_cases = [case for case in junit_cases if case["status"] in {"失败", "错误"}]

    status_class = "ok" if returncode == 0 else "bad"
    overall = "通过" if returncode == 0 else "未通过"

    def endpoint_row(endpoint: Dict[str, object]) -> str:
        methods = ", ".join(endpoint.get("methods", []))
        return (
            "<tr>"
            f"<td>{html.escape(methods)}</td>"
            f"<td>{html.escape(str(endpoint.get('path', '')))}</td>"
            f"<td>{html.escape(str(endpoint.get('kind', '')))}</td>"
            f"<td>{html.escape(str(endpoint.get('summary', '')))}</td>"
            f"<td>{html.escape(str(endpoint.get('source', '')))}</td>"
            "</tr>"
        )

    def case_row(case: Dict[str, str]) -> str:
        row_class = {
            "通过": "ok",
            "失败": "bad",
            "错误": "bad",
            "跳过": "muted",
        }.get(case["status"], "")
        return (
            f"<tr class=\"{row_class}\">"
            f"<td>{html.escape(case['status'])}</td>"
            f"<td>{html.escape(case['classname'])}</td>"
            f"<td>{html.escape(case['name'])}</td>"
            f"<td>{html.escape(case['time'])} 秒</td>"
            f"<td>{html.escape(case['message'])}</td>"
            "</tr>"
        )

    def request_case_row(case: Dict[str, Any]) -> str:
        assertion_count = len(case.get("assertions") or [])
        skip_reason = case.get("skip_reason") or ""
        redacted_request = truncate_and_redact(case.get("redacted_request", {}), 1000)
        return (
            "<tr>"
            f"<td>{html.escape(str(case.get('method', '')))}</td>"
            f"<td>{html.escape(str(case.get('url', '')))}</td>"
            f"<td>{html.escape(str(case.get('source_kind', '')))}</td>"
            f"<td>{html.escape(str(case.get('source', '')))}</td>"
            f"<td>{html.escape(str(case.get('expected_status', '')))}</td>"
            f"<td>{html.escape(str(assertion_count))}</td>"
            f"<td>{html.escape(skip_reason)}</td>"
            f"<td><pre>{html.escape(redacted_request)}</pre></td>"
            "</tr>"
        )

    endpoint_rows = "\n".join(endpoint_row(endpoint) for endpoint in endpoints) or (
        "<tr><td colspan=\"5\">未发现接口</td></tr>"
    )
    case_rows = "\n".join(case_row(case) for case in junit_cases) or (
        "<tr><td colspan=\"5\">暂无测试用例结果</td></tr>"
    )
    request_case_rows = "\n".join(request_case_row(case) for case in request_cases) or (
        "<tr><td colspan=\"8\">暂无接口执行明细</td></tr>"
    )
    generated_rows = "\n".join(
        f"<li>{html.escape(str(path))}</li>" for path in generated_tests
    ) or "<li>暂无生成文件</li>"
    failure_rows = "\n".join(
        f"<li><strong>{html.escape(case['name'])}</strong>：{html.escape(case['message'] or '未提供失败信息')}</li>"
        for case in failed_cases
    ) or "<li>没有失败用例</li>"
    stdout_block = html.escape(str(pytest_stdout)).strip() or "无标准输出"
    stderr_block = html.escape(str(pytest_stderr)).strip() or "无错误输出"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>接口测试报告</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f6f8fb; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin-top: 32px; font-size: 20px; }}
    .meta {{ color: #64748b; margin-bottom: 24px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }}
    .tile {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
    .tile strong {{ display: block; font-size: 26px; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 11px 12px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; word-break: break-word; }}
    th {{ background: #eef2f7; color: #334155; }}
    tr:last-child td {{ border-bottom: 0; }}
    ul, pre {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; margin: 0; padding: 16px 24px; }}
    pre {{ white-space: pre-wrap; overflow-x: auto; }}
    details {{ margin-bottom: 12px; }}
    summary {{ cursor: pointer; font-weight: 600; margin-bottom: 8px; }}
    .ok {{ color: #047857; }}
    .bad {{ color: #b42318; }}
    .muted {{ color: #64748b; }}
  </style>
</head>
<body>
<main>
  <h1>接口测试报告</h1>
  <div class="meta">项目目录：{html.escape(str(project_root)) or "未记录"} ｜ 生成时间：{html.escape(str(generated_at))}</div>
  <section class="summary">
    <div class="tile">整体结果<strong class="{status_class}">{overall}</strong></div>
    <div class="tile">用例总数<strong>{total}</strong></div>
    <div class="tile">通过<strong class="ok">{passed}</strong></div>
    <div class="tile">失败<strong class="bad">{failed + errors}</strong></div>
    <div class="tile">跳过<strong class="muted">{skipped}</strong></div>
    <div class="tile">成功率<strong>{success_rate}</strong></div>
    <div class="tile">总耗时<strong>{total_duration:.2f} 秒</strong></div>
    <div class="tile">发现接口<strong>{len(endpoints)}</strong></div>
  </section>

  <h2>失败摘要</h2>
  <ul>{failure_rows}</ul>

  <h2>测试结果</h2>
  <table>
    <thead><tr><th>状态</th><th>测试类</th><th>用例名称</th><th>耗时</th><th>信息</th></tr></thead>
    <tbody>{case_rows}</tbody>
  </table>

  <h2>发现的接口</h2>
  <table>
    <thead><tr><th>方法</th><th>路径</th><th>来源类型</th><th>说明</th><th>来源文件</th></tr></thead>
    <tbody>{endpoint_rows}</tbody>
  </table>

  <h2>接口执行明细</h2>
  <table>
    <thead><tr><th>方法</th><th>URL</th><th>来源类型</th><th>来源</th><th>期望状态码</th><th>断言数</th><th>跳过原因</th><th>脱敏请求参数</th></tr></thead>
    <tbody>{request_case_rows}</tbody>
  </table>

  <h2>生成的测试文件</h2>
  <ul>{generated_rows}</ul>

  <h2>运行日志</h2>
  <details open>
    <summary>pytest 标准输出</summary>
    <pre>{stdout_block}</pre>
  </details>
  <details>
    <summary>pytest 错误输出</summary>
    <pre>{stderr_block}</pre>
  </details>
</main>
</body>
</html>
"""


def save_chinese_report(summary: Dict[str, object], report_path: Path, html_report_path: Path) -> None:
    html_report_path.parent.mkdir(parents=True, exist_ok=True)
    junit_cases = load_junit_cases(report_path)
    html_report_path.write_text(build_chinese_report(summary, junit_cases), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and run API smoke tests from project source.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root used for auto detection")
    parser.add_argument("--frontend-dir", type=Path, help="Frontend source directory")
    parser.add_argument("--backend-dir", type=Path, help="Backend source directory")
    parser.add_argument("--extra-files", type=Path, nargs="*", help="Additional source files to scan")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Generated test output directory")
    parser.add_argument("--report-file", type=Path, default=DEFAULT_REPORT_FILE, help="pytest JUnit XML report path")
    parser.add_argument("--summary-file", type=Path, default=DEFAULT_SUMMARY_FILE, help="Run summary path")
    parser.add_argument("--html-report-file", type=Path, default=DEFAULT_HTML_REPORT_FILE, help="Chinese HTML report path")
    parser.add_argument("--mode", choices=["generic", "api"], default="api", help="Generation mode")
    parser.add_argument("--api-base-url", default=API_DEFAULT_BASE_URL, help="Base URL for API tests")
    parser.add_argument("--openapi-path", type=Path, help="OpenAPI/Swagger file or directory")
    parser.add_argument("--service-command", help="Command used to start the service under test")
    parser.add_argument("--service-health-url", default=None, help="Health check URL for the service under test")
    parser.add_argument("--service-timeout", type=int, default=30, help="Service startup timeout in seconds")
    parser.add_argument("--data-driven", action="store_true", help="Generate a parameterized test matrix")
    parser.add_argument("--case-config", type=Path, help="YAML file with endpoint request data, assertions, and safety settings")
    parser.add_argument("--allow-mutating-methods", action="store_true", help="Allow POST/PUT/PATCH/DELETE only when endpoint config also sets allow_mutating: true")
    parser.add_argument("--dry-run", action="store_true", help="Only scan sources and write the summary")
    args = parser.parse_args()

    missing_dependencies = check_runtime_dependencies()
    if missing_dependencies:
        packages = " ".join(missing_dependencies)
        print(
            f"缺少运行依赖：{', '.join(missing_dependencies)}。请先执行：pip install {packages}",
            file=sys.stderr,
        )
        return 1

    project_root = args.project_root.resolve()
    auto_detected = not any([args.frontend_dir, args.backend_dir, args.openapi_path, args.extra_files])
    detected_inputs = detect_project_inputs(project_root) if auto_detected else {
        "frontend_paths": [],
        "backend_paths": [],
        "openapi_paths": [],
    }

    frontend_paths = []
    backend_paths = []
    openapi_paths = []
    if args.frontend_dir:
        frontend_paths.append(args.frontend_dir)
    if args.backend_dir:
        backend_paths.append(args.backend_dir)
    if args.openapi_path:
        openapi_paths.append(args.openapi_path)
    if auto_detected:
        frontend_paths.extend(detected_inputs["frontend_paths"])
        backend_paths.extend(detected_inputs["backend_paths"])
        openapi_paths.extend(detected_inputs["openapi_paths"])

    extra_files = args.extra_files or []

    frontend = collect_source(dedupe_paths(frontend_paths + extra_files), FRONTEND_EXTENSIONS)
    backend = collect_source(dedupe_paths(backend_paths + extra_files), BACKEND_EXTENSIONS)
    openapi_docs = load_openapi_documents(dedupe_paths(openapi_paths))
    try:
        case_config = load_case_config(args.case_config)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError if yaml is not None else Exception) as exc:
        print(f"读取 case config 失败：{exc}", file=sys.stderr)
        return 1

    if not frontend and not backend and not openapi_docs and not case_config.get("endpoints"):
        print(
            "未找到可扫描的源码或 OpenAPI 文档。请检查路径，或显式传入 --frontend-dir、--backend-dir、--openapi-path。",
            file=sys.stderr,
        )
        return 1

    endpoints = endpoints_with_case_config(extract_api_endpoints({**frontend, **backend}, openapi_docs), case_config)
    request_cases = [build_request_case(endpoint, args.api_base_url, case_config, args.allow_mutating_methods) for endpoint in endpoints]
    summary = {
        "frontend_files": list(frontend.keys()),
        "backend_files": list(backend.keys()),
        "openapi_files": list(openapi_docs.keys()),
        "detected_endpoints": endpoints,
        "request_cases": request_cases,
        "auto_detected": auto_detected,
        "project_root": str(project_root),
        "scan_inputs": {
            "frontend_paths": [str(path) for path in dedupe_paths(frontend_paths)],
            "backend_paths": [str(path) for path in dedupe_paths(backend_paths)],
            "openapi_paths": [str(path) for path in dedupe_paths(openapi_paths)],
            "extra_files": [str(path) for path in extra_files],
        },
        "mode": args.mode,
        "api_base_url": args.api_base_url,
        "case_config_file": str(args.case_config) if args.case_config else None,
        "allow_mutating_methods": args.allow_mutating_methods,
        "generated_at": datetime.utcnow().isoformat(),
    }

    if args.dry_run:
        save_summary(summary, args.summary_file)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    service_process = None
    if args.service_command:
        try:
            service_process = start_service(args.service_command, Path.cwd(), args.service_health_url, args.service_timeout)
            summary["service_started"] = True
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    try:
        tests = generate_tests(
            frontend,
            backend,
            args.mode,
            args.api_base_url,
            openapi_docs,
            args.data_driven,
            case_config,
            args.allow_mutating_methods,
        )
        written_files = write_tests(tests, args.output_dir)
        summary["generated_tests"] = [str(path) for path in written_files]

        result = run_pytest(args.output_dir, args.report_file)
        summary["pytest_returncode"] = result["returncode"]
        summary["pytest_stdout"] = result["stdout"]
        summary["pytest_stderr"] = result["stderr"]
        summary["report_file"] = result["report_path"]
        summary["html_report_file"] = str(args.html_report_file)
        save_summary(summary, args.summary_file)
        save_chinese_report(summary, args.report_file, args.html_report_file)
    finally:
        if service_process:
            service_process.terminate()
            try:
                service_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service_process.kill()
                service_process.wait(timeout=5)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return result["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
