#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
轻量 API 客户端封装

目标：
1. 从当前目录的 .env 读取已有 API Key
2. 为 OpenAI / Serper / Groq / DeepSeek 提供最小可用的 Python 调用函数
3. 函数命名清晰、参数显式，便于后续挂到本地多 Agent 系统

依赖：
pip install requests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env_file(env_path: Path = ENV_PATH) -> Dict[str, str]:
    """读取 .env，不依赖 python-dotenv，避免额外安装。"""
    env: Dict[str, str] = {}
    if not env_path.exists():
        return env

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_env_value(name: str, env: Optional[Dict[str, str]] = None, required: bool = True) -> str:
    """
    优先从系统环境变量读取，其次读本地 .env。
    这样后续部署到服务器时，不用改代码。
    """
    env = env or load_env_file()
    value = os.getenv(name) or env.get(name, "")
    if required and not value:
        raise ValueError(f"缺少环境变量: {name}")
    return value


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    """统一处理 POST 请求和错误信息。"""
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:1000]
        raise RuntimeError(f"API 请求失败: {url}\n状态码: {response.status_code}\n响应: {detail}") from exc
    return response.json()


def _mask_key(value: str) -> str:
    """仅用于调试日志，不在业务逻辑中回传明文。"""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def call_openai_chat(
    messages: List[Dict[str, str]],
    model: str = "gpt-5.4-mini",
    temperature: float = 0.2,
    max_tokens: int = 1200,
    api_key: Optional[str] = None,
    base_url: str = "https://api.openai.com/v1/chat/completions",
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    OpenAI 对话调用。
    适合：主 Agent 总结、最终终审、高价值决策节点。
    """
    api_key = api_key or get_env_value("OPENAI_API_KEY")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return _post_json(base_url, headers, payload, timeout=timeout)


def search_serper_web(
    query: str,
    gl: str = "us",
    hl: str = "en",
    num: int = 10,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Serper 通用网页搜索。
    适合：爆款趋势摸底、竞品页面检索、跨平台搜索。
    注意：你当前 .env 中是 SERPER_API_KEY，不是 SERPAPI_API_KEY。
    """
    api_key = api_key or get_env_value("SERPER_API_KEY")
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": gl, "hl": hl, "num": num}
    return _post_json(url, headers, payload, timeout=timeout)


def search_serper_news(
    query: str,
    gl: str = "us",
    hl: str = "en",
    num: int = 10,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Serper 新闻搜索，适合新领域趋势和热点验证。"""
    api_key = api_key or get_env_value("SERPER_API_KEY")
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": gl, "hl": hl, "num": num}
    return _post_json(url, headers, payload, timeout=timeout)


def search_serper_patents(
    query: str,
    gl: str = "us",
    hl: str = "en",
    num: int = 10,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Serper 专利搜索。
    适合：专利前置预警阶段做公开网页级别的快速摸底。
    """
    api_key = api_key or get_env_value("SERPER_API_KEY")
    url = "https://google.serper.dev/patents"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": gl, "hl": hl, "num": num}
    return _post_json(url, headers, payload, timeout=timeout)


def call_groq_chat(
    messages: List[Dict[str, str]],
    model: str = "llama-3.1-8b-instant",
    temperature: float = 0.2,
    max_tokens: int = 1200,
    api_key: Optional[str] = None,
    base_url: str = "https://api.groq.com/openai/v1/chat/completions",
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Groq 对话调用，接口格式兼容 OpenAI。
    适合：低成本改写、批量分类、步骤化拆解、快速摘要。
    """
    api_key = api_key or get_env_value("GROQ_API_KEY")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return _post_json(base_url, headers, payload, timeout=timeout)


def call_deepseek_chat(
    messages: List[Dict[str, str]],
    model: str = "deepseek-v4-flash",
    temperature: float = 0.2,
    max_tokens: int = 1200,
    api_key: Optional[str] = None,
    base_url: str = "https://api.deepseek.com/chat/completions",
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    DeepSeek 对话调用。
    适合：大批量结构化输出、特征抽取、低成本候选生成。
    """
    api_key = api_key or get_env_value("DEEPSEEK_API_KEY")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return _post_json(base_url, headers, payload, timeout=timeout)


def extract_text_from_chat_response(provider: str, response_json: Dict[str, Any]) -> str:
    """
    从常见 OpenAI 兼容响应中提取文本。
    这样主流程里就不用针对每个平台写重复解析。
    """
    try:
        choice = response_json["choices"][0]
        message = choice.get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "\n".join([p for p in parts if p])
    except Exception as exc:
        raise ValueError(f"{provider} 响应格式无法解析: {json.dumps(response_json, ensure_ascii=False)[:500]}") from exc
    return ""


def get_budget_router() -> Dict[str, Dict[str, str]]:
    """
    给多 Agent 工作流一个简单的“默认路由建议”。
    这里只返回建议，不直接发请求，方便主程序自行接入。
    """
    return {
        "agent_trend": {
            "primary": "serper_web + deepseek-v4-flash",
            "fallback": "groq llama-3.1-8b-instant",
        },
        "agent_new_domain": {
            "primary": "serper_news + deepseek-v4-flash",
            "fallback": "groq llama-3.3-70b-versatile",
        },
        "agent_feature_transfer": {
            "primary": "deepseek-v4-flash",
            "fallback": "groq llama-3.1-8b-instant",
        },
        "agent_scenario": {
            "primary": "groq llama-3.1-8b-instant",
            "fallback": "deepseek-v4-flash",
        },
        "agent_ip_guard": {
            "primary": "serper_patents + openai gpt-5.4-mini",
            "fallback": "deepseek-v4-flash + 人工复核",
        },
        "main_agent_final_review": {
            "primary": "openai gpt-5.4-mini",
            "fallback": "groq openai/gpt-oss-20b",
        },
    }


def debug_loaded_keys() -> Dict[str, str]:
    """返回遮罩后的 key 视图，方便本地确认读取成功。"""
    env = load_env_file()
    visible = {}
    for name in [
        "OPENAI_API_KEY",
        "SERPER_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_MODEL_NAME",
        "CREWAI_TRACING_ENABLED",
    ]:
        value = env.get(name, "")
        visible[name] = _mask_key(value) if "KEY" in name and value else value
    return visible


if __name__ == "__main__":
    print(json.dumps(debug_loaded_keys(), ensure_ascii=False, indent=2))
    print(json.dumps(get_budget_router(), ensure_ascii=False, indent=2))
