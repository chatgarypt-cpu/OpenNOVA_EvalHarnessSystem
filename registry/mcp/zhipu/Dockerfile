# 使用官方 Python 镜像作为基础
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目文件
COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY src/ ./src/

# 使用 uv 同步依赖
RUN uv sync --frozen --no-cache

# 暴露端口
EXPOSE 8000

# 设置环境变量默认值
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PATH_PREFIX=/mcp

# 启动命令 - 监听 0.0.0.0 以便容器外部访问
CMD ["uv", "run", "zhipu-mcp", "--host", "0.0.0.0", "--port", "8000", "--path", "/mcp"]

