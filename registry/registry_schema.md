# Registry Schema R0

> 最小 schema 定义，覆盖 skill / MCP / hook / executor / plugin 五类能力。
> 每个 registry item 必须包含以下通用字段；标记为 optional / recommended 的字段除外。
> R0 registry 使用固定版本号 `0.1`。新条目 recommended 写入 `version: '0.1'`；历史 R0 条目未写入 version 时按 `0.1` 解释。

## 通用字段（五类能力共用）

```yaml
id: string                    # 唯一标识，如 skill-web-research
name: string                  # 人类可读名称
type: skill | mcp | hook | executor | plugin | gate_script | utility | resolver
status: active | candidate | to_be_filled | deprecated | retired
version: string               # recommended；R0 固定为 '0.1'，缺省时按 '0.1' 解释
purpose: string               # 一句话说明能力用途
applicable_lanes:
  - A_LINE_FORMAL
  - B_LINE_LIGHTWEIGHT_DAG
  - WORKYB_RUNTIME
  - DOGFOOD_TEST
  - COURSEWORK
  - EXPERIMENT
  - PRODUCTIVITY
  - ALL
inputs: list[string]          # 能力需要的输入
outputs: list[string]         # 能力产生的输出
allowed_paths: list[string]   # 允许操作的文件/目录
forbidden_paths: list[string] # 禁止操作的文件/目录
tools_required: list[string]  # 依赖的工具（如 codex, claude, python）
permission_level: readonly | write_within_scope | full
risk_level: low | medium | high | critical
owner_approval_required: bool
validation: list[string]      # 如何验证能力调用成功
return_format: string         # 能力返回的格式描述
asset_kind: skill | mcp | hook | executor | plugin   # optional；用于区分类别
evidence_paths: list[string]  # 证据文件路径模式
depends_on: list[string]      # optional；依赖的其他 registry 条目 ID
used_by: list[string]         # optional；被哪些 registry 条目引用
```

## Skill 特有字段

```yaml
skill_type: hermes | cc_switch | adarian_builtin
skill_path: string            # SKILL.md 路径
hermes_tool_provided: list[string]  # 提供的 tool 列表
load_required: bool           # 使用前是否必须 skill_view()
```

## MCP 特有字段

```yaml
transport: stdio | http
command: string               # stdio 模式启动命令
url: string                   # HTTP 模式 URL
server_name: string           # MCP server 名称
tools_provided: list[string]  # MCP 提供的工具列表
env_required: list[string]    # optional；运行所需环境变量，如 BRAVE_API_KEY
```

## Hook 特有字段

```yaml
hook_type: shell | python | python_script | http
trigger: string               # 触发事件（pre_llm_call, post_llm_call 等）
hook_path: string             # 脚本路径
auto_accept: bool
timeout_sec: int
```

## Executor 特有字段

```yaml
executor_registry_name: string  # executor_registry.py 中注册的名称
execution_model: tmux_interactive | managed_subprocess | http_proxy
module_path: string             # 模块路径
entry_class: string             # 类名
features: list[object]          # optional；能力开关或运行特性
env_required: list[string]      # optional；运行所需环境变量
```

## 枚举值定义

### applicable_lanes

| 值 | 含义 |
|----|------|
| A_LINE_FORMAL | 正式 A 线任务流 |
| B_LINE_LIGHTWEIGHT_DAG | 轻量 DAG / B 线任务流 |
| WORKYB_RUNTIME | Workyb runtime 内部能力 |
| DOGFOOD_TEST | Dogfood / 端到端测试能力 |
| COURSEWORK | 课程、作业、学习研究场景 |
| EXPERIMENT | 实验性能力或待验证工具链 |
| PRODUCTIVITY | 个人效率、办公、系统集成场景 |
| ALL | 适用于所有 lane 的通用能力 |

### status

| 值 | 含义 |
|----|------|
| active | 已确认可用 |
| candidate | 已注册但未验证 |
| to_be_filled | 计划中，路径/配置待补 |
| deprecated | 已弃用 |
| retired | 已移除 |

### permission_level

| 值 | 含义 |
|----|------|
| readonly | 只读，不修改任何文件 |
| write_within_scope | 允许在 allowed_paths 内写文件 |
| full | 无限制（仅限已知安全能力） |

### risk_level

| 值 | 含义 |
|----|------|
| low | 副作用可控 |
| medium | 可能修改文件或产生外部调用 |
| high | 涉及权限、网络、密钥 |
| critical | 涉及安全检查、代码执行、生产环境 |

### hook_type

| 值 | 含义 |
|----|------|
| shell | shell 脚本或 shell 命令 |
| python | Python 模块或 Python 运行时 hook |
| python_script | 直接执行的 `.py` 脚本 |
| http | HTTP hook |

### execution_model

| 值 | 含义 |
|----|------|
| tmux_interactive | tmux 交互式执行 |
| managed_subprocess | 受控 subprocess 执行 |
| http_proxy | HTTP proxy 型执行器 |

## 字段规则

1. 必填字段不得为 null / 空字符串；optional / recommended 字段可省略。
2. 不确定的路径必须标记 `TO_BE_FILLED`，不得猜测。
3. `status=candidate` 或 `to_be_filled` 的能力不得声称可用。
4. 五类能力分别存入独立 YAML 文件（plugin 归入 executor_registry.yaml 或独立文件）。
5. 通用字段在每类 registry 中重复，便于独立读取。

## 13. Acceptance

### 13.1 Hard Acceptance

- R0 条目缺省 `version` 时按固定 schema 版本 `0.1` 处理；新条目 recommended 显式写入 `version: '0.1'`。
- `applicable_lanes` 必须使用本文件列出的枚举值。
- `hook_path` 指向 `.py` 文件且直接执行时，`hook_type` 使用 `python_script`。
- executor `execution_model` 允许 `tmux_interactive`、`managed_subprocess`、`http_proxy`。
- MCP / executor 可声明 optional `env_required`；executor 可声明 optional `features`。
