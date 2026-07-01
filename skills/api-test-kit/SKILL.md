---
name: api-test-kit
description: Generate and run safe, configuration-driven API tests from frontend/backend source files or OpenAPI documents, then produce pytest JUnit output, summary JSON, and an enhanced Chinese HTML report. Use when the user asks to discover API endpoints, generate API tests, run API smoke tests, verify backend APIs, inspect Flask/FastAPI/Django/Express/Spring routes, add YAML-configured API assertions, or create Chinese test reports for a code project.
---

# API Test Kit

Use this skill to discover API endpoints, generate safe pytest API tests, run them, and produce an enhanced Chinese HTML test report.

## Workflow

1. Inspect the target project briefly for service startup commands, health check URLs, and framework clues.
2. Prefer the bundled `scripts/api_test_kit.py` script for endpoint discovery, test generation, pytest execution, dependency checks, and report creation.
3. Run the script from the target project root so generated files land in that project by default.
4. Start with automatic project detection unless the user gives explicit paths. Use `--project-root .`; the script searches common frontend/backend directories and OpenAPI files while ignoring build output, `node_modules`, `.git`, virtualenvs, and prior artifacts.
5. If auto detection misses files or finds the wrong area, rerun with explicit inputs:
   - Use `--frontend-dir` for JS/TS/Vue source containing `fetch` or `axios` calls.
   - Use `--backend-dir` for Python, Java/Kotlin, or JS/TS backend source.
   - Use `--openapi-path` for OpenAPI or Swagger JSON/YAML files.
6. If the API service is not already running, use `--service-command` and `--service-health-url` when the startup command and health endpoint are known.
7. Prefer a user-provided `--case-config case_config.yaml` when business headers, path params, payloads, expected status codes, or assertions are needed.
8. After running, summarize detected interface count, executed count, passed count, failed count, skipped count, report paths, generated tests, and any dependency or service-startup issues.

## Supported Detection

The script detects these common patterns:

- Flask and FastAPI decorators such as `@app.get("/path")`, `@router.post("/path")`, and `@app.route(..., methods=[...])`
- Django `path("route/", ...)` and `re_path(...)` route declarations
- Express style `app.get("/path", ...)` and `router.post("/path", ...)`
- Spring `@GetMapping`, `@PostMapping`, and `@RequestMapping(... RequestMethod.POST)` routes
- Frontend `fetch(...)` and `axios.get/post/put/delete/patch(...)` API calls
- OpenAPI `paths` entries in JSON/YAML documents

## Safety Rules

- Default execution is safe-method only: GET, HEAD, and OPTIONS.
- POST, PUT, PATCH, and DELETE are skipped unless the command includes `--allow-mutating-methods` and the endpoint config also sets `allow_mutating: true`.
- Do not default to production environments. Ask for or use a local, test, staging, or explicitly approved base URL.
- Do not fabricate test results. If pytest did not truly run successfully, do not say the tests passed.
- If required path parameters cannot be determined, report the skip reason and ask the user to add `path_params` to the config.
- Sensitive headers and payload fields such as Authorization, Cookie, Token, Password, Secret, and Api-Key must stay redacted in logs and reports.

## Command Patterns

Auto-detect project inputs first:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --project-root . \
  --api-base-url http://localhost:5000
```

Preview detected files and endpoints without running tests:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --project-root . \
  --dry-run
```

Run against explicit frontend and backend source:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --frontend-dir path/to/frontend \
  --backend-dir path/to/backend \
  --api-base-url http://localhost:5000
```

Run with a service startup command:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --backend-dir path/to/backend \
  --service-command "python app.py" \
  --service-health-url http://localhost:5000/health \
  --api-base-url http://localhost:5000
```

Run from OpenAPI documents:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --openapi-path path/to/openapi.yaml \
  --api-base-url http://localhost:5000
```

Run with YAML configuration:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --openapi-path path/to/openapi.yaml \
  --api-base-url http://localhost:5000 \
  --case-config case_config.yaml
```

Run mutating endpoints only with double opt-in:

```bash
python /path/to/api-test-kit/scripts/api_test_kit.py \
  --openapi-path path/to/openapi.yaml \
  --api-base-url http://localhost:5000 \
  --case-config case_config.yaml \
  --allow-mutating-methods
```

Example `case_config.yaml`:

```yaml
version: 1
defaults:
  timeout: 10
  expected_status: [200]
endpoints:
  "POST /users":
    enabled: true
    allow_mutating: true
    headers:
      Authorization: "Bearer ${ENV:API_TOKEN}"
    json:
      username: test_user
    expected_status: [201]
    assertions:
      - path: code
        op: eq
        value: 0
      - path: data.id
        op: exists
  "GET /users/{user_id}":
    path_params:
      user_id: 1
    expected_status: [200]
```

## Outputs

By default, the script writes these paths under the current working directory:

- `artifacts/generated-tests/` for generated pytest files
- `artifacts/junit.xml` for CI-compatible JUnit output
- `artifacts/summary.json` for machine-readable run details
- `artifacts/report.html` for the primary Chinese human-readable report

The Chinese report includes overall status, success rate, duration, failed-case summary, detected endpoints, endpoint method and URL, endpoint source, HTTP status behavior, assertion count, skip reason, failure details, redacted request data, generated files, and pytest stdout/stderr logs. Use `--html-report-file` to customize the report path.

## Operating Notes

- If dependencies are missing, install the packages named in the Chinese error message before rerunning.
- Prefer `artifacts/report.html` when the user asks for a readable report.
- Keep `artifacts/junit.xml` when CI integration matters.
- If local server startup or port binding fails due to sandboxing, rerun the same command with appropriate execution approval.
- If no endpoints are detected, report that clearly and suggest providing an OpenAPI file or explicit source directories.
- Final user-facing output must include interface count, executed count, passed count, failed count, skipped count, and report paths.
