#!/usr/bin/env python3
"""Generate and run lightweight API smoke tests from source files."""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml

FRONTEND_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".vue"}
BACKEND_EXTENSIONS = {".py", ".java", ".kt", ".go", ".rb", ".cs"}
OPENAPI_EXTENSIONS = {".json", ".yaml", ".yml"}
DEFAULT_OUTPUT_DIR = Path("artifacts/generated-tests")
DEFAULT_REPORT_FILE = Path("artifacts/junit.xml")
DEFAULT_SUMMARY_FILE = Path("artifacts/summary.json")
API_DEFAULT_BASE_URL = "http://localhost:8000"
SERVICE_STARTUP_POLL_INTERVAL = 0.5


def collect_source(paths: List[Path], extensions: set) -> Dict[str, str]:
    files = {}
    for path in paths:
        if path.is_file() and path.suffix in extensions:
            files[str(path)] = path.read_text(encoding="utf-8")
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix in extensions:
                    files[str(child)] = child.read_text(encoding="utf-8")
    return files


def load_openapi_documents(paths: List[Path]) -> Dict[str, dict]:
    openapi_docs = {}
    for path in paths:
        candidates = [path] if path.is_file() else path.rglob("*")
        for child in candidates:
            if not child.is_file() or child.suffix.lower() not in OPENAPI_EXTENSIONS:
                continue
            try:
                text = child.read_text(encoding="utf-8")
                openapi_docs[str(child)] = json.loads(text) if child.suffix.lower() == ".json" else yaml.safe_load(text)
            except (OSError, json.JSONDecodeError, yaml.YAMLError):
                continue
    return openapi_docs


def extract_openapi_endpoints(openapi_docs: Dict[str, dict]) -> List[Dict[str, object]]:
    endpoints = []
    for path, document in openapi_docs.items():
        if not isinstance(document, dict):
            continue
        for route, methods in document.get("paths", {}).items():
            if not isinstance(methods, dict):
                continue
            for method, spec in methods.items():
                if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    continue
                endpoints.append({
                    "source": path,
                    "path": route,
                    "methods": [method.upper()],
                    "kind": "openapi",
                    "summary": spec.get("summary", "") if isinstance(spec, dict) else "",
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
        elif suffix in FRONTEND_EXTENSIONS:
            endpoints.extend(parse_frontend_api_calls(path, code))
    return dedupe_endpoints(endpoints)


def parse_python_backend_endpoints(path: str, code: str) -> List[Dict[str, object]]:
    endpoints = []
    route_pattern = re.compile(
        r"@(?:app|router)\.route\(['\"](?P<path>[^'\"]+)['\"],\s*methods\s*=\s*\[(?P<methods>[^\]]+)\]",
        re.IGNORECASE,
    )
    method_pattern = re.compile(
        r"@(?:app|router)\.(?P<method>get|post|put|delete|patch)\(['\"](?P<path>[^'\"]+)['\"]",
        re.IGNORECASE,
    )
    for match in route_pattern.finditer(code):
        methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", match.group("methods"), re.IGNORECASE)
        endpoints.append({
            "source": path,
            "path": match.group("path"),
            "methods": [method.upper() for method in methods] or ["GET"],
            "kind": "backend",
        })
    for match in method_pattern.finditer(code):
        endpoints.append({
            "source": path,
            "path": match.group("path"),
            "methods": [match.group("method").upper()],
            "kind": "backend",
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


def generate_tests(
    frontend: Dict[str, str],
    backend: Dict[str, str],
    mode: str = "api",
    api_base_url: str = API_DEFAULT_BASE_URL,
    openapi_docs: Optional[Dict[str, dict]] = None,
    data_driven: bool = False,
) -> List[Dict[str, str]]:
    if mode != "api":
        return [{
            "id": "project_placeholder",
            "title": "Project smoke test",
            "description": "Placeholder test for projects without detected API endpoints.",
            "code": "def test_project_placeholder():\n    assert True\n",
        }]

    endpoints = extract_api_endpoints({**frontend, **backend}, openapi_docs)
    if not endpoints:
        return [{
            "id": "api_placeholder",
            "title": "API smoke test placeholder",
            "description": "No API endpoints were detected.",
            "code": "def test_api_placeholder():\n    assert True\n",
        }]

    if data_driven:
        entries = ",\n".join(
            f"    ('{endpoint['path']}', '{endpoint['methods'][0]}', {default_payload(endpoint['methods'][0])})"
            for endpoint in endpoints
        )
        return [{
            "id": "api_smoke_matrix",
            "title": "API smoke test matrix",
            "description": "Parameterized smoke checks for detected endpoints.",
            "code": (
                "import pytest\n"
                "import requests\n\n"
                f"BASE_URL = \"{api_base_url}\"\n\n"
                "@pytest.mark.parametrize('path,method,payload', [\n"
                f"{entries}\n"
                "])\n"
                "def test_api_endpoint_smoke(path, method, payload):\n"
                "    response = requests.request(method, f\"{BASE_URL}{path}\", json=payload if payload else None)\n"
                "    assert response.status_code < 500\n"
            ),
        }]

    tests = []
    for index, endpoint in enumerate(endpoints, start=1):
        method = endpoint["methods"][0].lower()
        request_block = ""
        request_payload = ""
        if method in {"post", "put", "patch"}:
            request_block = (
                "    json_payload = {\n"
                "        \"name\": \"example\",\n"
                "        \"age\": 1,\n"
                "    }\n"
            )
            request_payload = ", json=json_payload"
        test_name = make_test_name(endpoint["path"], method)
        tests.append({
            "id": f"api_{index}_{test_name}",
            "title": f"{endpoint['methods'][0]} {endpoint['path']} smoke test",
            "description": f"Smoke check for {endpoint['methods'][0]} {endpoint['path']}.",
            "code": (
                "import requests\n\n"
                f"BASE_URL = \"{api_base_url}\"\n\n"
                f"def test_{test_name}():\n"
                f"{request_block}"
                f"    response = requests.{method}(f\"{{BASE_URL}}{endpoint['path']}\"{request_payload})\n"
                "    assert response.status_code in (200, 201, 204, 400, 404)\n"
            ),
        })
    return tests


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and run API smoke tests from project source.")
    parser.add_argument("--frontend-dir", type=Path, help="Frontend source directory")
    parser.add_argument("--backend-dir", type=Path, help="Backend source directory")
    parser.add_argument("--extra-files", type=Path, nargs="*", help="Additional source files to scan")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Generated test output directory")
    parser.add_argument("--report-file", type=Path, default=DEFAULT_REPORT_FILE, help="pytest JUnit XML report path")
    parser.add_argument("--summary-file", type=Path, default=DEFAULT_SUMMARY_FILE, help="Run summary path")
    parser.add_argument("--mode", choices=["generic", "api"], default="api", help="Generation mode")
    parser.add_argument("--api-base-url", default=API_DEFAULT_BASE_URL, help="Base URL for API tests")
    parser.add_argument("--openapi-path", type=Path, help="OpenAPI/Swagger file or directory")
    parser.add_argument("--service-command", help="Command used to start the service under test")
    parser.add_argument("--service-health-url", default=None, help="Health check URL for the service under test")
    parser.add_argument("--service-timeout", type=int, default=30, help="Service startup timeout in seconds")
    parser.add_argument("--data-driven", action="store_true", help="Generate a parameterized test matrix")
    parser.add_argument("--dry-run", action="store_true", help="Only scan sources and write the summary")
    args = parser.parse_args()

    source_paths = []
    if args.frontend_dir:
        source_paths.append(args.frontend_dir)
    if args.backend_dir:
        source_paths.append(args.backend_dir)
    source_paths.extend(args.extra_files or [])

    frontend = collect_source(source_paths, FRONTEND_EXTENSIONS)
    backend = collect_source(source_paths, BACKEND_EXTENSIONS)
    openapi_docs = load_openapi_documents([args.openapi_path] if args.openapi_path else [])

    if not frontend and not backend and not openapi_docs:
        print("No source or OpenAPI documents were found. Check the provided paths.", file=sys.stderr)
        return 1

    endpoints = extract_api_endpoints({**frontend, **backend}, openapi_docs)
    summary = {
        "frontend_files": list(frontend.keys()),
        "backend_files": list(backend.keys()),
        "openapi_files": list(openapi_docs.keys()),
        "detected_endpoints": endpoints,
        "mode": args.mode,
        "api_base_url": args.api_base_url,
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

    tests = generate_tests(frontend, backend, args.mode, args.api_base_url, openapi_docs, args.data_driven)
    written_files = write_tests(tests, args.output_dir)
    summary["generated_tests"] = [str(path) for path in written_files]

    result = run_pytest(args.output_dir, args.report_file)
    summary["pytest_returncode"] = result["returncode"]
    summary["pytest_stdout"] = result["stdout"]
    summary["pytest_stderr"] = result["stderr"]
    summary["report_file"] = result["report_path"]
    save_summary(summary, args.summary_file)

    if service_process:
        service_process.terminate()
        service_process.wait(timeout=5)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return result["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
