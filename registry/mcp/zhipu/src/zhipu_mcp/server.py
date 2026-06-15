import os
from typing import Annotated
from enum import Enum

import httpx
from fastmcp import FastMCP

# 智谱AI API配置
ZHIPU_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_WEB_SEARCH_ENDPOINT = f"{ZHIPU_API_BASE}/web_search"

# 创建 FastMCP 服务器实例
mcp = FastMCP(
    "智谱AI MCP",
    instructions="智谱AI MCP服务器，提供网络搜索等能力。",
)


class SearchEngine(str, Enum):
    """支持的搜索引擎"""
    SEARCH_STD = "search_std"  # 智谱基础版搜索引擎
    SEARCH_PRO = "search_pro"  # 智谱高阶版搜索引擎
    SEARCH_PRO_SOGOU = "search_pro_sogou"  # 搜狗
    SEARCH_PRO_QUARK = "search_pro_quark"  # 夸克搜索


class RecencyFilter(str, Enum):
    """时间范围过滤"""
    ONE_DAY = "oneDay"  # 一天内
    ONE_WEEK = "oneWeek"  # 一周内
    ONE_MONTH = "oneMonth"  # 一个月内
    ONE_YEAR = "oneYear"  # 一年内
    NO_LIMIT = "noLimit"  # 不限


class ContentSize(str, Enum):
    """内容详细程度"""
    MEDIUM = "medium"  # 返回摘要信息
    HIGH = "high"  # 最大化上下文


def get_api_key() -> str:
    """获取智谱AI API密钥"""
    api_key = os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        raise ValueError(
            "请设置环境变量 ZHIPU_API_KEY，可在 https://bigmodel.cn/usercenter/proj-mgmt/apikeys 获取"
        )
    return api_key


async def _call_web_search_api(
    query: str,
    search_engine: str = SearchEngine.SEARCH_STD.value,
    search_intent: bool = False,
    count: int = 10,
    search_domain_filter: str | None = None,
    search_recency_filter: str = RecencyFilter.NO_LIMIT.value,
    content_size: str = ContentSize.MEDIUM.value,
) -> dict:
    """
    调用智谱AI网络搜索API
    
    Args:
        query: 搜索查询内容，建议不超过70个字符
        search_engine: 搜索引擎类型
        search_intent: 是否进行搜索意图识别
        count: 返回结果条数，范围1-50
        search_domain_filter: 白名单域名过滤
        search_recency_filter: 时间范围过滤
        content_size: 内容详细程度
    
    Returns:
        搜索结果字典
    """
    api_key = get_api_key()
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "search_query": query,
        "search_engine": search_engine,
        "search_intent": search_intent,
        "count": min(max(count, 1), 50),  # 确保在有效范围内
        "search_recency_filter": search_recency_filter,
        "content_size": content_size,
    }
    
    if search_domain_filter:
        payload["search_domain_filter"] = search_domain_filter
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            ZHIPU_WEB_SEARCH_ENDPOINT,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def _format_search_results(data: dict) -> str:
    """格式化搜索结果为可读文本"""
    output_lines = []
    
    # 搜索意图信息
    if "search_intent" in data and data["search_intent"]:
        output_lines.append("## 搜索意图分析")
        for intent in data["search_intent"]:
            output_lines.append(f"- 原始查询: {intent.get('query', 'N/A')}")
            output_lines.append(f"- 意图类型: {intent.get('intent', 'N/A')}")
            if intent.get('keywords'):
                output_lines.append(f"- 改写关键词: {intent.get('keywords')}")
        output_lines.append("")
    
    # 搜索结果
    if "search_result" in data and data["search_result"]:
        output_lines.append("## 搜索结果")
        output_lines.append("")
        
        for i, result in enumerate(data["search_result"], 1):
            title = result.get("title", "无标题")
            link = result.get("link", "")
            content = result.get("content", "无内容摘要")
            media = result.get("media", "")
            publish_date = result.get("publish_date", "")
            
            output_lines.append(f"### {i}. {title}")
            if media:
                output_lines.append(f"**来源**: {media}")
            if publish_date:
                output_lines.append(f"**发布时间**: {publish_date}")
            output_lines.append(f"**链接**: {link}")
            output_lines.append("")
            output_lines.append(content)
            output_lines.append("")
            output_lines.append("---")
            output_lines.append("")
    else:
        output_lines.append("未找到相关搜索结果。")
    
    return "\n".join(output_lines)


@mcp.tool()
async def web_search(
    query: Annotated[str, "搜索查询内容，建议不超过70个字符"],
    search_engine: Annotated[
        str,
        "搜索引擎类型: search_std(智谱基础版), search_pro(智谱高阶版), search_pro_sogou(搜狗), search_pro_quark(夸克)"
    ] = "search_std",
    search_intent: Annotated[
        bool,
        "是否进行搜索意图识别。true=执行意图识别后再搜索，false=直接搜索"
    ] = False,
    count: Annotated[int, "返回结果条数，范围1-50"] = 10,
    search_domain_filter: Annotated[
        str | None,
        "白名单域名过滤，仅返回指定域名的结果（如 www.example.com）"
    ] = None,
    search_recency_filter: Annotated[
        str,
        "时间范围过滤: oneDay(一天内), oneWeek(一周内), oneMonth(一个月内), oneYear(一年内), noLimit(不限)"
    ] = "noLimit",
    content_size: Annotated[
        str,
        "内容详细程度: medium(摘要信息), high(详细内容)"
    ] = "medium",
) -> str:
    """
    使用智谱AI网络搜索引擎搜索互联网内容。
    
    这是一个专为大模型设计的搜索引擎，具有增强的意图识别能力，
    返回结构化的搜索结果，包括网页标题、URL、摘要、来源等信息。
    
    支持多种搜索引擎：
    - search_std: 智谱基础版搜索引擎（默认）
    - search_pro: 智谱高阶版搜索引擎
    - search_pro_sogou: 搜狗搜索
    - search_pro_quark: 夸克搜索
    
    可以通过时间范围过滤获取最新内容，也可以指定域名白名单限制搜索范围。
    """
    try:
        result = await _call_web_search_api(
            query=query,
            search_engine=search_engine,
            search_intent=search_intent,
            count=count,
            search_domain_filter=search_domain_filter,
            search_recency_filter=search_recency_filter,
            content_size=content_size,
        )
        return _format_search_results(result)
    
    except httpx.HTTPStatusError as e:
        error_msg = f"API请求失败: HTTP {e.response.status_code}"
        try:
            error_detail = e.response.json()
            if "error" in error_detail:
                error_msg += f"\n错误信息: {error_detail['error'].get('message', str(error_detail))}"
        except Exception:
            error_msg += f"\n响应内容: {e.response.text}"
        return error_msg
    
    except ValueError as e:
        return f"配置错误: {str(e)}"
    
    except Exception as e:
        return f"搜索出错: {str(e)}"


def main():
    """主入口 - 使用 stdio 协议启动服务器"""
    import argparse
    
    parser = argparse.ArgumentParser(description="智谱AI MCP服务器")
    parser.add_argument("--stdio", action="store_true", default=True, help="使用 stdio 传输 (默认)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (HTTP 模式)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (HTTP 模式)")
    parser.add_argument("--path", default="/mcp", help="MCP端点路径 (HTTP 模式)")
    parser.add_argument("--http", action="store_true", help="使用 HTTP 传输模式")
    
    args = parser.parse_args()
    
    if args.http:
        print("启动智谱AI MCP服务器 (HTTP 模式)...")
        print(f"地址: http://{args.host}:{args.port}{args.path}")
        mcp.run(transport="http", host=args.host, port=args.port, path=args.path)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
