# 智谱AI MCP 服务器

基于 [智谱AI Web Search API](https://docs.bigmodel.cn/api-reference/工具-api/网络搜索) 实现的 MCP (Model Context Protocol) 服务器，为大模型提供网络搜索能力。

**使用 Streamable HTTP 协议传输**

## 功能特性

- 🔍 **多搜索引擎支持**：智谱基础版、高阶版、搜狗、夸克
- 🎯 **意图识别**：可选的搜索意图识别，优化搜索结果
- ⏰ **时间过滤**：支持一天/一周/一个月/一年内的时间范围过滤
- 🌐 **域名过滤**：支持白名单域名限制
- 📊 **结构化输出**：返回标题、URL、摘要、来源等信息
- 🚀 **Streamable HTTP**：使用高效的 HTTP 流式传输协议

## 安装

### 使用 uv（推荐）

```bash
cd zhipu_mcp
uv sync
```

### 使用 pip

```bash
cd zhipu_mcp
pip install -e .
```

## 配置

### 1. 获取 API Key

前往 [智谱AI开放平台](https://bigmodel.cn/usercenter/proj-mgmt/apikeys) 获取 API Key。

### 2. 设置环境变量

```bash
export ZHIPU_API_KEY="your-api-key-here"
```

## 启动服务器

```bash
# 使用默认配置启动 (127.0.0.1:8000/mcp)
uv run zhipu-websearch-mcp

# 或指定参数
uv run zhipu-websearch-mcp --host 0.0.0.0 --port 9000 --path /api/mcp
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `127.0.0.1` | 监听地址 |
| `--port` | `8000` | 监听端口 |
| `--path` | `/mcp` | MCP端点路径 |

## 配置 MCP 客户端

### Claude Desktop 配置

编辑 Claude Desktop 配置文件：

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

添加以下配置（使用 Streamable HTTP URL）：

```json
{
  "mcpServers": {
    "zhipu": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

> 注意：需要先启动 MCP 服务器，再启动 Claude Desktop。

### Cursor 配置

在 Cursor 的 MCP 设置中添加：

```json
{
  "zhipu": {
    "url": "http://127.0.0.1:8000/mcp"
  }
}
```

## 工具说明

### web_search - 网络搜索

使用智谱AI网络搜索引擎搜索互联网内容。

#### 参数

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | ✅ | - | 搜索查询内容，建议不超过70个字符 |
| `search_engine` | string | ❌ | `search_std` | 搜索引擎类型 |
| `search_intent` | boolean | ❌ | `false` | 是否进行搜索意图识别 |
| `count` | integer | ❌ | `10` | 返回结果条数，范围1-50 |
| `search_domain_filter` | string | ❌ | - | 白名单域名过滤 |
| `search_recency_filter` | string | ❌ | `noLimit` | 时间范围过滤 |
| `content_size` | string | ❌ | `medium` | 内容详细程度 |

#### 搜索引擎类型

| 值 | 说明 |
|----|------|
| `search_std` | 智谱基础版搜索引擎 |
| `search_pro` | 智谱高阶版搜索引擎 |
| `search_pro_sogou` | 搜狗搜索 |
| `search_pro_quark` | 夸克搜索 |

#### 时间范围过滤

| 值 | 说明 |
|----|------|
| `oneDay` | 一天内 |
| `oneWeek` | 一周内 |
| `oneMonth` | 一个月内 |
| `oneYear` | 一年内 |
| `noLimit` | 不限（默认） |

#### 内容详细程度

| 值 | 说明 |
|----|------|
| `medium` | 返回摘要信息，满足常规问答需求 |
| `high` | 最大化上下文，信息量大，适合需要细节的场景 |

## 使用示例

在 Claude 或 Cursor 中，你可以这样使用：

```
请搜索最近一周关于"人工智能"的新闻
```

MCP 将自动调用 `web_search` 工具，返回结构化的搜索结果。

## Docker 部署

### 使用 Docker Compose（本地测试）

```bash
# 设置环境变量
export ZHIPU_API_KEY="your-api-key-here"

# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 使用 Dokploy 部署

1. 在 Dokploy 中创建新项目
2. 选择 **Git Repository** 或 **Docker** 部署方式
3. 配置构建设置：
   - 如果使用 Git Repository，Dokploy 会自动检测 Dockerfile
   - 端口设置为 `8000`
4. 在 **Environment Variables** 中添加：
   - `ZHIPU_API_KEY`: 你的智谱 AI API 密钥
5. 部署后，MCP 端点地址为：`http://your-domain:8000/mcp`

#### Dokploy 环境变量配置

| 变量名 | 必需 | 说明 |
|--------|------|------|
| `ZHIPU_API_KEY` | ✅ | 智谱AI API密钥 |

### 手动 Docker 部署

```bash
# 构建镜像
docker build -t zhipu-mcp .

# 运行容器
docker run -d \
  --name zhipu-mcp \
  -p 8000:8000 \
  -e ZHIPU_API_KEY="your-api-key-here" \
  zhipu-mcp
```

## 开发

### 运行测试

```bash
uv run python -c "from zhipu_mcp.server import mcp; print('Import successful')"
```

### 调试模式

可以使用 MCP Inspector 进行调试：

```bash
npx @anthropic-ai/mcp-inspector
```

然后在 Inspector 中连接到 `http://127.0.0.1:8000/mcp`。

## 许可证

MIT License

## 相关链接

- [智谱AI开放平台](https://open.bigmodel.cn/)
- [Web Search API 文档](https://docs.bigmodel.cn/api-reference/工具-api/网络搜索)
- [MCP 协议](https://modelcontextprotocol.io/)
- [FastMCP](https://github.com/jlowin/fastmcp)
