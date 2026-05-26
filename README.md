# API Test Kit

API Test Kit scans source files or OpenAPI documents, detects API endpoints, generates lightweight pytest smoke tests, and writes a JUnit report for CI.

## Features

- Detects Flask-style Python routes and common frontend `fetch`/`axios` calls.
- Reads OpenAPI JSON/YAML documents.
- Generates pytest + requests smoke tests.
- Can start a local service, wait for a health check, run generated tests, and save a report.

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the sample project:

```bash
python samples/backend/app.py
```

In another terminal, generate and run API tests:

```bash
python api_test_kit.py \
  --frontend-dir samples/frontend \
  --backend-dir samples/backend \
  --api-base-url http://localhost:5000 \
  --service-health-url http://localhost:5000/health
```

Or run the demo script:

```bash
bash run_demo.sh
```

Generated test files and reports are written to `artifacts/` by default.

## CLI

```bash
api-test-kit --help
```

Common options:

- `--frontend-dir`: frontend source directory to scan.
- `--backend-dir`: backend source directory to scan.
- `--openapi-path`: OpenAPI/Swagger file or directory.
- `--api-base-url`: base URL used by generated API tests.
- `--service-command`: command used to start the service under test.
- `--service-health-url`: URL checked before tests run.
- `--data-driven`: generate one parameterized test matrix.
- `--dry-run`: scan inputs and write a summary without generating tests.

## Development

Run the test suite:

```bash
pytest
```

Install locally in editable mode:

```bash
pip install -e .
```

## License

This project is released under the MIT License. See `LICENSE` for details.
