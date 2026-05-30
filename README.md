# API Test Kit

API Test Kit 是一个轻量级接口测试工具。它可以扫描源码或 OpenAPI 文档，识别 API 端点，生成 `pytest + requests` 风格的冒烟测试，并输出可用于 CI 的 JUnit 报告。

## 功能特性

- 支持自动识别常见项目目录，未传目录时会从项目根目录扫描。
- 支持识别 Flask / FastAPI / Django / Express / Spring 常见路由写法。
- 支持识别前端代码中的常见 `fetch` / `axios` 接口调用。
- 支持读取 OpenAPI JSON/YAML 文档。
- 自动生成轻量级 API 冒烟测试。
- 可启动本地待测服务，等待健康检查通过后再执行测试。
- 默认输出生成测试、JUnit 报告、中文 HTML 报告和运行摘要到 `artifacts/`。

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

启动示例后端服务：

```bash
python samples/backend/app.py
```

在另一个终端生成并运行接口测试：

```bash
python api_test_kit.py \
  --frontend-dir samples/frontend \
  --backend-dir samples/backend \
  --api-base-url http://localhost:5000 \
  --service-health-url http://localhost:5000/health
```

也可以让工具从当前项目自动识别前后端目录：

```bash
python api_test_kit.py --project-root . --dry-run
```

也可以直接运行演示脚本：

```bash
bash run_demo.sh
```

生成的测试文件、测试报告和运行摘要默认会写入 `artifacts/`。其中 `artifacts/report.html` 是中文可读报告，`artifacts/junit.xml` 用于 CI 系统读取。

## 命令行用法

安装为本地包后可以使用：

```bash
api-test-kit --help
```

常用参数：

- `--frontend-dir`：要扫描的前端源码目录。
- `--backend-dir`：要扫描的后端源码目录。
- `--project-root`：自动识别项目结构时使用的项目根目录，默认当前目录。
- `--openapi-path`：OpenAPI/Swagger 文件或目录。
- `--api-base-url`：生成测试使用的 API 基础地址。
- `--service-command`：启动待测服务的命令。
- `--service-health-url`：服务启动后的健康检查地址。
- `--data-driven`：生成参数化测试矩阵。
- `--html-report-file`：中文 HTML 报告输出路径，默认 `artifacts/report.html`。
- `--dry-run`：只扫描输入并写入摘要，不生成和执行测试。

## 作为 Codex Skill 安装

本仓库同时包含可发布的 Codex skill 包，路径为 `skills/api-test-kit`。可以从 GitHub 仓库路径安装：

```bash
python scripts/install-skill-from-github.py \
  --repo yajuntang/AI_test_skill \
  --path skills/api-test-kit
```

安装后重启 Codex，即可通过 `$api-test-kit` 调用该技能。

## 本地开发

运行测试：

```bash
pytest
```

以可编辑模式安装：

```bash
pip install -e .
```

## 许可证

本项目基于 MIT License 开源，详见 `LICENSE`。
