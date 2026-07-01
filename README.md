# API Test Kit

API Test Kit 是一个安全、配置驱动的轻量级接口测试工具。它可以扫描源码或 OpenAPI 文档，识别 API 端点，生成 `pytest + requests` 风格的接口测试，并输出可用于 CI 的 JUnit 报告。

## 功能特性

- 支持自动识别常见项目目录，未传目录时会从项目根目录扫描。
- 支持识别 Flask / FastAPI / Django / Express / Spring 常见路由写法。
- 支持识别前端代码中的常见 `fetch` / `axios` 接口调用。
- 支持读取 OpenAPI JSON/YAML 文档。
- 支持通过 YAML 配置请求头、Query、路径参数、JSON、Form、超时、期望状态码和业务断言。
- 默认只执行 GET、HEAD、OPTIONS；POST、PUT、PATCH、DELETE 需要 CLI 和接口配置双重授权。
- 支持 `${ENV:NAME}` 环境变量替换，并对 Authorization、Cookie、Token、Password、Secret、Api-Key 等敏感内容脱敏。
- 支持 JSON Path 业务断言：`eq`、`ne`、`exists`、`not_exists`、`not_empty`、`contains`、`in`、`type`、`regex`、`length`。
- 自动生成轻量级 API 测试。
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

生成的测试文件、测试报告和运行摘要默认会写入 `artifacts/`。其中 `artifacts/report.html` 是中文可读报告，`artifacts/junit.xml` 用于 CI 系统读取。报告包含接口方法和 URL、接口来源、HTTP 状态码、请求耗时、断言结果、跳过原因、失败类型、脱敏后的请求参数。响应内容最多保留 2000 字符，超出部分会截断并脱敏。

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
- `--case-config`：读取 YAML 接口用例配置。
- `--allow-mutating-methods`：允许执行修改类请求。仍要求对应接口配置 `allow_mutating: true`。
- `--html-report-file`：中文 HTML 报告输出路径，默认 `artifacts/report.html`。
- `--dry-run`：只扫描输入并写入摘要，不生成和执行测试。

## YAML 配置示例

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

使用配置运行：

```bash
python api_test_kit.py \
  --openapi-path openapi.yaml \
  --api-base-url http://localhost:5000 \
  --case-config case_config.yaml
```

执行修改类接口时必须同时满足两个条件：

```bash
python api_test_kit.py \
  --openapi-path openapi.yaml \
  --api-base-url http://localhost:5000 \
  --case-config case_config.yaml \
  --allow-mutating-methods
```

如果缺少 `--allow-mutating-methods` 或接口未设置 `allow_mutating: true`，POST、PUT、PATCH、DELETE 会跳过并在报告中显示原因。

## 参数生成优先级

请求参数按以下顺序生成：

1. `case_config.yaml`
2. OpenAPI `example`
3. OpenAPI `default`
4. OpenAPI `enum`
5. 根据字段类型生成默认值

必填路径参数无法确定时，测试会跳过并提示补充 `path_params`。

## 项目限制

- 工具不会默认调用生产环境。请显式传入本地、测试或预发环境的 `--api-base-url`。
- 无配置时会保持旧版状态码兼容策略；配置了 `expected_status` 后会严格按配置校验。
- 工具不会伪造测试结果；只有 `pytest` 真实执行成功才表示测试通过。
- 业务断言面向 JSON 响应；非 JSON 响应不能执行 JSON Path 断言。
- 修改类请求默认跳过，避免误写真实数据。

## 作为 Codex Skill 安装

本仓库同时包含可发布的 Codex skill 包，路径为 `skills/api-test-kit`。可以从 GitHub 仓库路径安装：

```bash
git clone https://github.com/Cheryl-station/AI_test_skill.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R AI_test_skill/skills/api-test-kit "${CODEX_HOME:-$HOME/.codex}/skills/api-test-kit"
```

安装后重启 Codex，即可通过 `$api-test-kit` 调用该技能。

## 本地开发

运行测试：

```bash
pytest
```

同步根目录脚本到 skill 包：

```bash
python tools/sync_skill_script.py
python tools/sync_skill_script.py --check
```

以可编辑模式安装：

```bash
pip install -e .
```

## 许可证

本项目基于 MIT License 开源，详见 `LICENSE`。
