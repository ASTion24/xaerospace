# 通用 LLM Provider 配置

## 1. 目标

Assistant 不绑定某一个固定 API。运行时从版本化 JSON 中选择一个命名
Provider profile，再创建对应的 HTTP Provider。

当前内置的 Provider 类型是：

```text
openai_compatible
```

它可以连接任意实现 OpenAI-compatible `/chat/completions` 协议和结构化
JSON Schema 输出的服务，包括本地 llama.cpp、兼容网关和云端模型 API。

“任意 API”在这里指任意兼容该协议的地址、模型和鉴权配置。协议不兼容的服务
必须新增明确的 Provider 适配器类型，系统不会猜测请求或响应格式。

## 2. 文件约定

仓库只提交：

```text
config/providers.example.json
```

本地真实配置使用：

```text
config/providers.local.json
config/providers.<name>.local.json
```

这些本地文件已写入 `.gitignore`，不会作为 Git 提交的一部分。首次创建：

```bash
cp config/providers.example.json config/providers.local.json
chmod 600 config/providers.local.json
```

当前工作区已经创建一个 `previous_glm` 初始 profile，保存此前完成真实验收时
使用的地址和模型。该文件只存在于本地并且权限为 `0600`。

## 3. 配置结构

```json
{
  "schema_version": 1,
  "active_provider": "cloud",
  "providers": {
    "cloud": {
      "type": "openai_compatible",
      "base_url": "https://api.example.invalid/v1",
      "model": "replace-with-model-id",
      "api_key_env": "XAEROSPACE_PROVIDER_API_KEY",
      "compatibility_mode": "strict",
      "timeout_s": 45,
      "max_concurrency": 1,
      "max_output_tokens": 1024
    }
  }
}
```

顶层字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前固定为 `1` |
| `active_provider` | 未通过 CLI 指定时使用的 profile |
| `providers` | 1 至 32 个命名 profile |

profile 名称只允许字母、数字、点、下划线和连字符，最长 64 个字符。

## 4. OpenAI-compatible 字段

| 字段 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `type` | 是 | - | 当前为 `openai_compatible` |
| `base_url` | 是 | - | 绝对 HTTP(S) URL，不允许内嵌账号密码 |
| `model` | 是 | - | 发送到 API 的模型 ID |
| `api_key_env` | 否 | - | Bearer Token 所在环境变量名 |
| `header_env` | 否 | `{}` | 自定义 Header 到环境变量名的映射 |
| `chat_completions_path` | 否 | `/chat/completions` | 结构化生成路径 |
| `models_path` | 否 | `/models` | 模型发现和健康检查路径 |
| `compatibility_mode` | 否 | `strict` | `strict` 或 `llama_cpp` |
| `timeout_s` | 否 | `45` | 单次生成超时 |
| `max_concurrency` | 否 | `1` | Provider 最大并发，范围 1 至 8 |
| `health_timeout_s` | 否 | `10` | 健康检查超时 |
| `health_ttl_s` | 否 | `30` | 健康结果缓存时间 |
| `max_output_tokens` | 否 | `1024` | 最大输出 Token |
| `circuit_failure_threshold` | 否 | `3` | 打开熔断器前的连续失败次数 |
| `circuit_cooldown_s` | 否 | `60` | 熔断冷却时间 |

请求路径允许携带非敏感查询参数，例如：

```json
{
  "models_path": "/models?api-version=2026-01-01"
}
```

## 5. 密钥和鉴权

### 5.1 Bearer Token

配置只写环境变量名称：

```json
{
  "api_key_env": "XAEROSPACE_PROVIDER_API_KEY"
}
```

运行前设置：

```bash
export XAEROSPACE_PROVIDER_API_KEY="..."
```

系统发送：

```text
Authorization: Bearer <value>
```

### 5.2 自定义 Header

对于使用 `X-API-Key` 或组织标识的网关：

```json
{
  "header_env": {
    "X-API-Key": "XAEROSPACE_GATEWAY_API_KEY",
    "X-Organization": "XAEROSPACE_GATEWAY_ORGANIZATION"
  }
}
```

运行前设置对应环境变量。

### 5.3 安全约束

- 配置 Schema 不接受 `api_key` 明文字段；
- URL 不允许 `https://user:password@host/` 形式；
- 缺失任何被引用的环境变量时启动失败；
- Schema 错误不会把输入值回显到日志；
- `Content-Type` 和 `Host` 不能由 profile 覆盖；
- 同一 profile 不能同时声明 `api_key_env` 和自定义
  `Authorization` Header；
- API 地址、模型和密钥不应进入 tracked 文件、文档或测试夹具。

## 6. 选择配置和 profile

### 6.1 默认发现

`xaerospace web` 和 `xaerospace assistant-eval` 按顺序查找：

1. CLI `--provider-config`；
2. `XAEROSPACE_PROVIDER_CONFIG`；
3. 当前目录的 `config/providers.local.json`；
4. 源码项目目录的 `config/providers.local.json`；
5. `~/.config/xaerospace/providers.local.json`。

profile 选择顺序：

1. CLI `--provider-profile`；
2. `XAEROSPACE_PROVIDER_PROFILE`；
3. JSON 中的 `active_provider`。

### 6.2 CLI

```bash
xaerospace web \
  --provider-config config/providers.local.json \
  --provider-profile cloud
```

评测：

```bash
xaerospace assistant-eval \
  --provider-config config/providers.local.json \
  --provider-profile cloud \
  --concurrency 1 \
  --output outputs/cloud-evaluation.json
```

### 6.3 环境变量

```bash
export XAEROSPACE_PROVIDER_CONFIG="$PWD/config/providers.local.json"
export XAEROSPACE_PROVIDER_PROFILE="cloud"
xaerospace web
```

## 7. 多 API 示例

```json
{
  "schema_version": 1,
  "active_provider": "local",
  "providers": {
    "local": {
      "type": "openai_compatible",
      "base_url": "http://127.0.0.1:8080/v1",
      "model": "local-model",
      "compatibility_mode": "llama_cpp",
      "timeout_s": 120
    },
    "cloud": {
      "type": "openai_compatible",
      "base_url": "https://api.example.invalid/v1",
      "model": "cloud-model",
      "api_key_env": "XAEROSPACE_PROVIDER_API_KEY",
      "compatibility_mode": "strict"
    },
    "gateway": {
      "type": "openai_compatible",
      "base_url": "https://gateway.example.invalid/llm/v1",
      "model": "gateway-model",
      "header_env": {
        "X-API-Key": "XAEROSPACE_GATEWAY_API_KEY"
      },
      "chat_completions_path": "/structured/chat",
      "models_path": "/catalog/models"
    }
  }
}
```

切换 API 不需要修改 Python 代码：

```bash
xaerospace web --provider-profile local
xaerospace web --provider-profile cloud
xaerospace web --provider-profile gateway
```

## 8. 兼容模式

### `strict`

直接发送完整 Pydantic JSON Schema，适合正确实现
`response_format.type=json_schema` 的 API。

### `llama_cpp`

用于旧版 llama.cpp 兼容服务：

- 内联 `$defs/$ref`；
- 只保留支持的 grammar 关键字；
- 在 system message 中重复结构化 Schema；
- 关闭 thinking 输出。

无论使用哪一种传输模式，响应都会再次通过完整 Pydantic 模型、合同编译器、
任务族注册表和后端注册表验证。

## 9. Fail-closed 行为

系统不会在以下情况自动切换 profile：

- 配置文件不存在或不是合法 JSON；
- `active_provider` 不存在；
- 指定 profile 不存在；
- Provider 类型不受支持；
- 密钥环境变量缺失；
- URL、路径、范围或鉴权配置非法；
- 健康检查、请求或结构化输出失败。

选择的 API 失败时，Assistant 返回明确错误。手动合同和物理工作流仍可继续使用。

## 10. 直接环境变量配置

未选择 Provider JSON 时，以下变量可构造一个临时单 profile：

```text
XAEROSPACE_ASSISTANT_LLM_BASE_URL
XAEROSPACE_ASSISTANT_LLM_MODEL
XAEROSPACE_ASSISTANT_LLM_API_KEY
XAEROSPACE_ASSISTANT_LLM_TIMEOUT_S
XAEROSPACE_ASSISTANT_LLM_COMPATIBILITY_MODE
XAEROSPACE_ASSISTANT_LLM_MAX_CONCURRENCY
XAEROSPACE_ASSISTANT_LLM_MAX_OUTPUT_TOKENS
XAEROSPACE_ASSISTANT_LLM_HEALTH_TIMEOUT_S
XAEROSPACE_ASSISTANT_LLM_CIRCUIT_FAILURE_THRESHOLD
XAEROSPACE_ASSISTANT_LLM_CIRCUIT_COOLDOWN_S
```

Provider JSON 优先级更高。只要显式选择了 JSON，配置无效时就会失败，不会退回
直接环境变量或其他 profile。
