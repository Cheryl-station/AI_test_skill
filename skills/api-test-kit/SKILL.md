---
name: api-test-kit
description: Generate and run API smoke tests from frontend/backend source files or OpenAPI documents, then produce pytest JUnit output, summary JSON, and an enhanced Chinese HTML report. Use when the user asks to discover API endpoints, generate API tests, run API smoke tests, verify backend APIs, inspect Flask/FastAPI/Django/Express/Spring routes, or create Chinese test reports for a code project.
---

# API Test Kit

Use this skill to discover API endpoints, generate lightweight pytest smoke tests, run them, and produce an enhanced Chinese HTML test report.

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
7. After running, summarize the result, report path, generated tests, detected endpoints, and any dependency or service-startup issues.

## Supported Detection

The script detects these common patterns:

- Flask and FastAPI decorators such as `@app.get("/path")`, `@router.post("/path")`, and `@app.route(..., methods=[...])`
- Django `path("route/", ...)` and `re_path(...)` route declarations
- Express style `app.get("/path", ...)` and `router.post("/path", ...)`
- Spring `@GetMapping`, `@PostMapping`, and `@RequestMapping(... RequestMethod.POST)` routes
- Frontend `fetch(...)` and `axios.get/post/put/delete/patch(...)` API calls
- OpenAPI `paths` entries in JSON/YAML documents

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

## Outputs

By default, the script writes these paths under the current working directory:

- `artifacts/generated-tests/` for generated pytest files
- `artifacts/junit.xml` for CI-compatible JUnit output
- `artifacts/summary.json` for machine-readable run details
- `artifacts/report.html` for the primary Chinese human-readable report

The Chinese report includes overall status, success rate, duration, failed-case summary, detected endpoints, generated files, and pytest stdout/stderr logs. Use `--html-report-file` to customize the report path.

## Operating Notes

- If dependencies are missing, install the packages named in the Chinese error message before rerunning.
- Prefer `artifacts/report.html` when the user asks for a readable report.
- Keep `artifacts/junit.xml` when CI integration matters.
- If local server startup or port binding fails due to sandboxing, rerun the same command with appropriate execution approval.
- If no endpoints are detected, report that clearly and suggest providing an OpenAPI file or explicit source directories.
