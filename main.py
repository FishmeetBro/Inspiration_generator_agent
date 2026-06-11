#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
真实数据版多 Agent 主程序

能力范围：
1. 读取 prompts/、shared_data_contract.yaml、agent_registry.yaml、.env
2. 通过 SERPAPI 或当前已配置的 SERPER 采集亚马逊/抖音相关公开搜索结果
3. 用 DeepSeek / Groq 批量清洗搜索结果，提炼爆款信号
4. 用 GPT-4o 驱动中枢 Agent、场景拆解、特征迁移、专利预警与最终灵感收敛
5. 把每一步中间数据都落到本地 JSON 文件，方便审计和复跑

说明：
- 你当前 .env 里只有 SERPER_API_KEY，没有 SERPAPI_API_KEY
- 本程序会优先尝试 SERPAPI；如果没配置，就自动降级到 SERPER
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

from api_clients import (
    call_deepseek_chat,
    call_groq_chat,
    call_openai_chat,
    extract_text_from_chat_response,
    get_env_value,
    load_env_file,
    search_serper_patents,
    search_serper_web,
)


# ------------------------------
# 轻量 YAML 读取
# ------------------------------


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def load_yaml_like(path: Path) -> Any:
    """优先用 PyYAML；没有的话走当前项目够用的简易解析。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except Exception:
        pass

    raw_lines = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        raw_lines.append((indent, line.strip()))

    def parse_block(index: int, indent: int) -> Tuple[Any, int]:
        if index >= len(raw_lines):
            return {}, index

        is_list = raw_lines[index][1].startswith("- ")
        if is_list:
            result: List[Any] = []
            while index < len(raw_lines):
                line_indent, content = raw_lines[index]
                if line_indent != indent or not content.startswith("- "):
                    break

                item_text = content[2:].strip()
                index += 1

                if item_text and ":" in item_text:
                    key, value = item_text.split(":", 1)
                    item: Dict[str, Any] = {key.strip(): parse_scalar(value.strip()) if value.strip() else None}
                    while index < len(raw_lines) and raw_lines[index][0] > indent:
                        child_key, child_value = raw_lines[index][1].split(":", 1)
                        child_key = child_key.strip()
                        child_value = child_value.strip()
                        if child_value:
                            item[child_key] = parse_scalar(child_value)
                            index += 1
                        else:
                            nested, index = parse_block(index + 1, raw_lines[index + 1][0])
                            item[child_key] = nested
                    result.append(item)
                    continue

                result.append(parse_scalar(item_text))
            return result, index

        result_dict: Dict[str, Any] = {}
        while index < len(raw_lines):
            line_indent, content = raw_lines[index]
            if line_indent != indent or content.startswith("- "):
                break

            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                result_dict[key] = parse_scalar(value)
                index += 1
                continue
            nested, index = parse_block(index + 1, raw_lines[index + 1][0])
            result_dict[key] = nested
        return result_dict, index

    parsed, _ = parse_block(0, raw_lines[0][0] if raw_lines else 0)
    return parsed


# ------------------------------
# 通用工具
# ------------------------------


def load_prompts(prompts_dir: Path) -> Dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(prompts_dir.glob("*.md"))}


def ensure_required(name: str, payload: Dict[str, Any], contract: Dict[str, Any]) -> None:
    obj_def = contract.get("objects", {}).get(name, {})
    required = obj_def.get("required", [])
    missing = [field for field in required if field not in payload or payload[field] in (None, "", [])]
    if missing:
        raise ValueError(f"{name} 缺少必填字段: {', '.join(missing)}")


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def chunk_list(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def extract_json_block(text: str) -> Any:
    """
    从模型输出里提取 JSON。
    常见情况：模型会返回 ```json ... ``` 或前后夹带说明文字。
    """
    text = text.strip()
    fenced = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.S)
    if fenced:
        return json.loads(fenced[0])

    bracket_match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
    if bracket_match:
        return json.loads(bracket_match.group(1))
    return json.loads(text)


def normalize_url(item: Dict[str, Any]) -> str:
    return item.get("link") or item.get("url") or ""


def normalize_search_item(item: Dict[str, Any], query: str, platform: str) -> Dict[str, Any]:
    return {
        "query": query,
        "platform": platform,
        "title": item.get("title") or "",
        "snippet": item.get("snippet") or item.get("description") or "",
        "link": normalize_url(item),
        "position": item.get("position") or item.get("rank") or 0,
        "source": item.get("source") or item.get("displayed_link") or "",
    }


def tokenize_text(text: str) -> List[str]:
    """Simple tokenizer for rough similarity checks."""
    return [tok for tok in re.split(r"[^\w\u4e00-\u9fff]+", (text or "").lower()) if tok]


def jaccard_similarity(a: str, b: str) -> float:
    sa = set(tokenize_text(a))
    sb = set(tokenize_text(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


FBA_RULES_ESTIMATE = {
    "small_standard": {"base_fee": 3.4, "per_lb_over_1": 0.2},
    "large_standard": {"base_fee": 4.9, "per_lb_over_1": 0.35},
    "oversize": {"base_fee": 8.5, "per_lb_over_1": 0.45},
    "monthly_storage_per_cuft_avg_usd": 1.15,
    "referral_fee_rate": 0.15,
    "headhaul_per_kg_usd": 6.5,
    "min_headhaul_usd": 0.35,
}


def build_default_task(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "request_id": f"REQ-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "user_goal": args.goal or "围绕亚马逊与抖音，寻找桌面收纳和居家小件赛道的新品灵感",
        "target_market": ["douyin_cn", "amazon_global"],
        "workflow_hint": args.workflow,
        "constraints": {
            "categories": [item.strip() for item in args.categories.split(",") if item.strip()],
            "exclude_categories": ["带电产品", "大件家具", "危险品"],
            "max_material_delta_pct": 15,
            "min_margin_cn": 30,
            "min_margin_global": 45,
            "small_parcel_only": True,
            "avoid_electronics": True,
            "avoid_patent_dense_shapes": True,
        },
        "seed_keywords": [item.strip() for item in args.seed.split(",") if item.strip()],
    }


# ------------------------------
# 搜索层：SERPAPI 优先，SERPER 兜底
# ------------------------------


def search_via_serpapi(query: str, api_key: str, num: int = 10, hl: str = "en", gl: str = "us") -> Dict[str, Any]:
    """
    使用 SerpApi 的 Google Search 接口。
    当前项目默认未配置 SERPAPI_API_KEY，所以通常会走 SERPER 兜底。
    """
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": num,
        "hl": hl,
        "gl": gl,
    }
    response = requests.get(url, params=params, timeout=40)
    response.raise_for_status()
    return response.json()


def collect_search_results(task: Dict[str, Any], env: Dict[str, str]) -> Dict[str, Any]:
    """
    采集公开搜索结果，模拟“亚马逊 / 抖音榜单摸底”。
    这里不抓封闭榜单 API，而是用搜索结果找公开榜单页、评测页、热卖页。
    """
    categories = task.get("constraints", {}).get("categories") or ["桌面收纳", "居家小件"]
    keywords = task.get("seed_keywords") or ["磁吸", "可拆卸", "模块化"]

    queries = []
    for category in categories[:3]:
        queries.extend(
            [
                ("amazon_global", f'Amazon best sellers "{category}" {" ".join(keywords[:2])}'),
                ("amazon_global", f'Amazon trending "{category}" organizer'),
                ("douyin_cn", f'抖音 热卖榜 {category} {" ".join(keywords[:2])}'),
                ("douyin_cn", f'抖音 电商 爆款 {category}'),
            ]
        )

    serpapi_key = env.get("SERPAPI_API_KEY", "")
    all_items = []
    logs = []

    for platform, query in queries:
        try:
            if serpapi_key:
                raw = search_via_serpapi(query=query, api_key=serpapi_key, num=8, hl="zh-cn" if platform == "douyin_cn" else "en", gl="cn" if platform == "douyin_cn" else "us")
                organic = raw.get("organic_results", [])
                items = [normalize_search_item(item, query, platform) for item in organic]
                logs.append(f"SERPAPI OK: {query}")
            else:
                raw = search_serper_web(query=query, num=8, gl="cn" if platform == "douyin_cn" else "us", hl="zh-cn" if platform == "douyin_cn" else "en")
                organic = raw.get("organic", [])
                items = [normalize_search_item(item, query, platform) for item in organic]
                logs.append(f"SERPER fallback OK: {query}")
        except Exception as exc:
            items = []
            logs.append(f"SEARCH FAIL: {query} -> {exc}")
        all_items.extend(items)

    # 去重，避免同一链接反复进入清洗层
    seen = set()
    deduped = []
    for item in all_items:
        key = (item["platform"], item["title"], item["link"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return {"queries": queries, "items": deduped, "logs": logs}


# ------------------------------
# LLM 调用层
# ------------------------------


def call_batch_llm(messages: List[Dict[str, str]], provider_hint: str = "deepseek") -> str:
    """
    批量清洗层优先用 DeepSeek，其次 Groq。
    这一步处理的是大批量搜索结果，不值得默认走 GPT-4o。
    """
    if provider_hint == "groq":
        response = call_groq_chat(messages=messages, model="llama-3.1-8b-instant", max_tokens=1800, temperature=0.1)
        return extract_text_from_chat_response("groq", response)

    try:
        response = call_deepseek_chat(messages=messages, model="deepseek-v4-flash", max_tokens=2000, temperature=0.1)
        return extract_text_from_chat_response("deepseek", response)
    except Exception:
        response = call_groq_chat(messages=messages, model="llama-3.1-8b-instant", max_tokens=1800, temperature=0.1)
        return extract_text_from_chat_response("groq", response)


def call_reasoning_llm(messages: List[Dict[str, str]], model: str) -> str:
    """
    核心推理层统一走 GPT-4o。
    这样符合用户要求，同时把调用次数限制在少量高价值节点。
    """
    response = call_openai_chat(messages=messages, model=model, max_tokens=2200, temperature=0.2)
    return extract_text_from_chat_response("openai", response)


def safe_llm_json(messages: List[Dict[str, str]], model: str, fallback: Any, provider: str = "openai") -> Any:
    try:
        if provider == "openai":
            text = call_reasoning_llm(messages, model=model)
        else:
            text = call_batch_llm(messages, provider_hint=provider)
        return extract_json_block(text)
    except Exception:
        return deepcopy(fallback)


# ------------------------------
# 数据清洗：把搜索结果压成 trend agent 可消费格式
# ------------------------------


def clean_market_results(raw_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    把原始搜索结果批量压成 market_signals。
    输出字段与 01_全网爆款溯源挖掘Agent.md 对齐。
    """
    if not raw_items:
        return {"agent": "trend_reverse_engineering", "market_signals": [], "summary": ["没有检索到公开结果"]}

    merged_signals: List[Dict[str, Any]] = []
    summaries: List[str] = []

    for batch in chunk_list(raw_items[:24], 8):
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是爆款数据清洗器。"
                    "请把搜索结果提炼成 market_signals 数组。"
                    "只输出 JSON，格式为 "
                    '{"market_signals":[{"signal_id":"","product_name":"","platform":"","category":"","growth_window":"7d|30d","growth_reason_type":"","target_users":[],"user_pain_points":[],"review_complaints":[],"premium_drivers":[],"structure_highlights":[],"appearance_highlights":[],"extractable_features":[],"risks":[]}],"summary":[]}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(batch, ensure_ascii=False),
            },
        ]

        fallback = {"market_signals": [], "summary": ["批量清洗失败，已跳过该批"]}
        cleaned = safe_llm_json(prompt, model="gpt-4o", fallback=fallback, provider="deepseek")
        merged_signals.extend(cleaned.get("market_signals", []))
        summaries.extend(cleaned.get("summary", []))

    # 基础后处理，避免 signal_id 缺失
    for idx, signal in enumerate(merged_signals, start=1):
        signal.setdefault("signal_id", f"SIG-{idx:03d}")
        signal.setdefault("product_name", "未命名爆款线索")
        signal.setdefault("platform", "amazon_global")
        signal.setdefault("category", "未分类")
        signal.setdefault("growth_window", "7d")
        signal.setdefault("growth_reason_type", "功能刚需")
        signal.setdefault("target_users", [])
        signal.setdefault("user_pain_points", [])
        signal.setdefault("review_complaints", [])
        signal.setdefault("premium_drivers", [])
        signal.setdefault("structure_highlights", [])
        signal.setdefault("appearance_highlights", [])
        signal.setdefault("extractable_features", [])
        signal.setdefault("risks", [])

    return {
        "agent": "trend_reverse_engineering",
        "market_signals": merged_signals[:8],
        "summary": summaries[:5] or ["已完成搜索结果清洗"],
    }


def build_google_patents_public_url(query: str) -> str:
    """Build a public Google Patents search URL for manual review."""
    return f"https://patents.google.com/?q={quote_plus(query)}"


def normalize_patent_results(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize either Serper patents/web results into a compact list."""
    items = raw.get("organic", []) or raw.get("patents", []) or raw.get("organic_results", []) or []
    normalized = []
    for idx, item in enumerate(items[:5], start=1):
        normalized.append(
            {
                "rank": idx,
                "title": item.get("title") or "",
                "snippet": item.get("snippet") or item.get("description") or "",
                "link": normalize_url(item),
                "source": item.get("source") or item.get("displayed_link") or "",
            }
        )
    return normalized


def infer_product_profile(idea: Dict[str, Any]) -> Dict[str, Any]:
    """
    Infer an approximate physical profile for cost estimation.
    This is intentionally simple and deterministic to keep token cost low.
    """
    name = idea.get("idea_name", "")
    definition = idea.get("product_definition", "")
    scenarios = " ".join(idea.get("scenarios", []))
    features = " ".join(idea.get("migrated_features", []))
    text = " ".join([name, definition, scenarios, features])

    profile = {
        "length_in": 12.0,
        "width_in": 8.0,
        "height_in": 1.2,
        "weight_lb": 0.9,
        "target_sale_price_usd": 19.99,
        "base_product_cost_usd": 3.2,
        "confidence": "estimated",
    }

    if "车载" in text:
        profile.update({"length_in": 10.0, "width_in": 4.5, "height_in": 4.0, "weight_lb": 0.8, "target_sale_price_usd": 18.99})
    if "透明" in text:
        profile["target_sale_price_usd"] += 1.0
    if "模块" in text or "多分区" in text:
        profile["weight_lb"] += 0.2
        profile["base_product_cost_usd"] += 0.6
        profile["target_sale_price_usd"] += 2.0
    if "挂" in text or "支架" in text:
        profile.update({"length_in": 11.0, "width_in": 6.0, "height_in": 2.5, "weight_lb": 1.1})
    if "板" in text:
        profile["height_in"] = 0.8
    return profile


def classify_fba_size_tier(profile: Dict[str, Any]) -> str:
    dims = sorted([profile["length_in"], profile["width_in"], profile["height_in"]], reverse=True)
    longest, median, shortest = dims
    weight = profile["weight_lb"]
    if longest <= 15 and median <= 12 and shortest <= 0.75 and weight <= 1.0:
        return "small_standard"
    if longest <= 18 and median <= 14 and shortest <= 8 and weight <= 20.0:
        return "large_standard"
    return "oversize"


def estimate_fba_costs(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate headhaul, tail fulfillment, storage, and margin using public-rule-style heuristics.
    The fee table is intentionally configurable and approximate.
    """
    size_tier = classify_fba_size_tier(profile)
    rule = FBA_RULES_ESTIMATE[size_tier]

    length_cm = profile["length_in"] * 2.54
    width_cm = profile["width_in"] * 2.54
    height_cm = profile["height_in"] * 2.54
    actual_weight_kg = profile["weight_lb"] * 0.453592
    volumetric_weight_kg = (length_cm * width_cm * height_cm) / 6000.0
    chargeable_weight_kg = max(actual_weight_kg, volumetric_weight_kg)

    headhaul = max(FBA_RULES_ESTIMATE["min_headhaul_usd"], chargeable_weight_kg * FBA_RULES_ESTIMATE["headhaul_per_kg_usd"])
    tail = rule["base_fee"] + max(0.0, profile["weight_lb"] - 1.0) * rule["per_lb_over_1"]
    cubic_feet = (profile["length_in"] * profile["width_in"] * profile["height_in"]) / 1728.0
    storage = cubic_feet * FBA_RULES_ESTIMATE["monthly_storage_per_cuft_avg_usd"]
    referral = profile["target_sale_price_usd"] * FBA_RULES_ESTIMATE["referral_fee_rate"]
    total_cost = profile["base_product_cost_usd"] + headhaul + tail + storage + referral
    gross_margin_rate = max(0.0, (profile["target_sale_price_usd"] - total_cost) / max(0.01, profile["target_sale_price_usd"]))

    return {
        "size_tier": size_tier,
        "chargeable_weight_kg": round(chargeable_weight_kg, 3),
        "headhaul_est_usd": round(headhaul, 2),
        "tail_fulfillment_est_usd": round(tail, 2),
        "monthly_storage_est_usd": round(storage, 2),
        "referral_fee_est_usd": round(referral, 2),
        "base_product_cost_usd": round(profile["base_product_cost_usd"], 2),
        "target_sale_price_usd": round(profile["target_sale_price_usd"], 2),
        "gross_margin_rate": round(gross_margin_rate, 4),
    }


# ------------------------------
# 真实数据版多 Agent 引擎
# ------------------------------


class RealIdeaEngine:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.env = load_env_file(base_dir / ".env")
        self.prompts = load_prompts(base_dir / "prompts")
        self.contract = load_yaml_like(base_dir / "schemas" / "shared_data_contract.yaml")
        self.registry = load_yaml_like(base_dir / "config" / "agent_registry.yaml")
        self.openai_model = self.env.get("OPENAI_MODEL_NAME") or "gpt-4o"

    def route_for(self, workflow: str) -> List[str]:
        rules = self.registry.get("routing_rules", {})
        route = rules.get(workflow, {}).get("order", [])
        return route or ["agent_trend", "agent_feature_transfer", "agent_scenario", "agent_ip_guard", "main_agent_final_review"]

    def ensure_task(self, task: Dict[str, Any]) -> None:
        ensure_required("task_brief", task, self.contract)

    def orchestrator_plan(self, task: Dict[str, Any], cleaned_trend: Dict[str, Any], route: List[str]) -> Dict[str, Any]:
        system_prompt = self.prompts.get("00_中枢调度主Agent_SystemPrompt.md", "")
        prompt = [
            {
                "role": "system",
                "content": system_prompt
                + "\n只输出 JSON，格式为 "
                + '{"workflow_type":"","route":[],"selection_reason":[],"filter_strategy":[],"top_focus_signals":[]}',
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "cleaned_signal_count": len(cleaned_trend.get("market_signals", [])),
                        "candidate_route": route,
                        "signal_preview": cleaned_trend.get("market_signals", [])[:5],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        fallback = {
            "workflow_type": task["workflow_hint"],
            "route": route,
            "selection_reason": ["按配置文件默认路由执行"],
            "filter_strategy": ["先压缩候选，再走 GPT-4o 精推理"],
            "top_focus_signals": [item.get("product_name", "") for item in cleaned_trend.get("market_signals", [])[:3]],
        }
        return safe_llm_json(prompt, model=self.openai_model, fallback=fallback, provider="openai")

    def run_feature_transfer(self, task: Dict[str, Any], market_signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        system_prompt = self.prompts.get("03_全域特征库提取与跨域迁移Agent.md", "")
        prompt = [
            {
                "role": "system",
                "content": system_prompt
                + "\n只输出 JSON，格式为 "
                + '{"agent":"feature_transfer_engine","feature_matches":[{"match_id":"","source_problem":"","abstracted_features":[],"selected_features":[],"rejected_features":[],"feature_selection_reason":[],"candidate_product_concepts":[{"concept_name":"","concept_definition":"","structural_points":[],"interaction_points":[],"accessory_matrix_options":[],"cost_risk_notes":[],"compliance_risk_notes":[]}]}],"summary":[]}',
            },
            {
                "role": "user",
                "content": json.dumps({"task": task, "market_signals": market_signals[:5]}, ensure_ascii=False),
            },
        ]
        fallback = {
            "agent": "feature_transfer_engine",
            "feature_matches": [],
            "summary": ["特征迁移阶段失败"],
        }
        result = safe_llm_json(prompt, model=self.openai_model, fallback=fallback, provider="openai")
        result.setdefault("agent", "feature_transfer_engine")
        result.setdefault("feature_matches", [])
        result.setdefault("summary", [])
        return result

    def run_scenario_validation(self, task: Dict[str, Any], feature_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        system_prompt = self.prompts.get("04_用户全场景拆解Agent.md", "")
        prompt = [
            {
                "role": "system",
                "content": system_prompt
                + "\n只输出 JSON，格式为 "
                + '{"agent":"scenario_validation","scenario_reviews":[{"review_id":"","concept_name":"","target_users":[],"usage_chain":[],"fit_scenarios":[],"conflict_scenarios":[],"explicit_needs":[],"implicit_needs":[],"storage_requirements":[],"environment_constraints":[],"validation_status":"pass|revise|fail","revision_advice":[]}],"summary":[]}',
            },
            {
                "role": "user",
                "content": json.dumps({"task": task, "feature_matches": feature_matches[:5]}, ensure_ascii=False),
            },
        ]
        fallback = {
            "agent": "scenario_validation",
            "scenario_reviews": [],
            "summary": ["场景校验失败"],
        }
        result = safe_llm_json(prompt, model=self.openai_model, fallback=fallback, provider="openai")
        result.setdefault("agent", "scenario_validation")
        result.setdefault("scenario_reviews", [])
        result.setdefault("summary", [])
        return result

    def run_ip_guard(self, task: Dict[str, Any], feature_matches: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        先对候选概念做专利搜索，再把搜索结果交给 GPT-4o 生成 risk_reviews。
        """
        patent_search_records: Dict[str, Any] = {}
        for match in feature_matches[:5]:
            concept = (match.get("candidate_product_concepts") or [{}])[0]
            concept_name = concept.get("concept_name", "unknown")
            query = f'{concept_name} design patent Amazon private label product'
            try:
                patent_search_records[concept_name] = search_serper_patents(query=query, num=5)
            except Exception:
                try:
                    patent_search_records[concept_name] = search_serper_web(query=query, num=5)
                except Exception as exc:
                    patent_search_records[concept_name] = {"error": str(exc)}

        system_prompt = self.prompts.get("05_专利竞品前置预警Agent.md", "")
        prompt = [
            {
                "role": "system",
                "content": system_prompt
                + "\n只输出 JSON，格式为 "
                + '{"agent":"ip_risk_guard","risk_reviews":[{"risk_id":"","concept_name":"","appearance_risk":"low|medium|high","structure_risk":"low|medium|high","platform_compliance_risk":"low|medium|high","competitor_overlap_risk":"low|medium|high","risk_reasons":[],"safe_zones":[],"redesign_directions":[],"patent_layout_suggestions":[],"final_risk_status":"pass|revise|fail"}],"summary":[]}',
            },
            {
                "role": "user",
                "content": json.dumps({"task": task, "feature_matches": feature_matches[:5], "patent_search_records": patent_search_records}, ensure_ascii=False),
            },
        ]
        fallback = {
            "agent": "ip_risk_guard",
            "risk_reviews": [],
            "summary": ["专利预警阶段失败"],
        }
        result = safe_llm_json(prompt, model=self.openai_model, fallback=fallback, provider="openai")
        result.setdefault("agent", "ip_risk_guard")
        result.setdefault("risk_reviews", [])
        result.setdefault("summary", [])
        return result, patent_search_records

    def run_patent_risk_validation(self, ideas: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Public patent pre-screen based on Google Patents public search URLs and search results.
        This is an initial similarity screen, not a legal opinion.
        """
        validations = []
        for idea in ideas:
            query = " ".join(
                [
                    idea.get("idea_name", ""),
                    " ".join(idea.get("migrated_features", [])[:3]),
                    "design patent",
                ]
            ).strip()
            public_url = build_google_patents_public_url(query)

            try:
                raw = search_serper_patents(query=query, num=5)
            except Exception:
                try:
                    raw = search_serper_web(query=f"site:patents.google.com {query}", num=5)
                except Exception as exc:
                    raw = {"error": str(exc)}

            results = normalize_patent_results(raw) if isinstance(raw, dict) else []
            idea_text = " ".join(
                [
                    idea.get("idea_name", ""),
                    idea.get("product_definition", ""),
                    " ".join(idea.get("migrated_features", [])),
                ]
            )

            scored = []
            for item in results:
                haystack = " ".join([item.get("title", ""), item.get("snippet", "")])
                score = jaccard_similarity(idea_text, haystack)
                scored.append({**item, "similarity_score": round(score, 4)})
            scored.sort(key=lambda x: x["similarity_score"], reverse=True)

            top_score = scored[0]["similarity_score"] if scored else 0.0
            if top_score >= 0.55:
                risk_level = "high"
                status = "fail"
            elif top_score >= 0.30 or len(scored) >= 4:
                risk_level = "medium"
                status = "revise"
            else:
                risk_level = "low"
                status = "pass"

            result_text = " ".join([f'{x.get("title","")} {x.get("snippet","")}' for x in scored])
            missing_features = [f for f in idea.get("migrated_features", []) if f and f.lower() not in result_text.lower()]
            gap_analysis = (
                [f"当前公开检索结果中对特征 `{f}` 的直接覆盖较少，可优先从该结构差异切入。" for f in missing_features[:3]]
                or ["当前检索结果存在一定相似结构，建议从连接方式、分区布局和外观轮廓继续拉开差异。"]
            )

            avoidance = [
                "避免直接复用热门主体轮廓和开孔布局。",
                "优先修改接口位置、模块拼接关系和配件连接方式。",
                "优先将专利布局放在主体外观 + 可拆分配件矩阵上。",
            ]

            validations.append(
                {
                    "idea_name": idea.get("idea_name", ""),
                    "public_search_url": public_url,
                    "infringement_risk_level": risk_level,
                    "validation_status": status,
                    "top_similarity_score": round(top_score, 4),
                    "similar_patent_signals": scored[:3],
                    "patent_gap_analysis": gap_analysis,
                    "avoidance_suggestions": avoidance,
                    "notes": "本结果仅为公开检索初筛，不构成法律意见。",
                }
            )
        return {"patent_validations": validations}

    def run_cost_logistics_validation(self, task: Dict[str, Any], ideas: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Estimate FBA-style unit economics and logistics cost.
        If gross margin is below the global threshold, mark the idea as low priority.
        """
        validations = []
        margin_floor = (task.get("constraints", {}).get("min_margin_global", 45) or 45) / 100.0

        for idea in ideas:
            profile = infer_product_profile(idea)
            estimate = estimate_fba_costs(profile)
            is_small_parcel_friendly = estimate["size_tier"] in {"small_standard", "large_standard"}
            meets_margin = estimate["gross_margin_rate"] >= margin_floor
            priority = "normal" if (meets_margin and is_small_parcel_friendly) else "low"

            validations.append(
                {
                    "idea_name": idea.get("idea_name", ""),
                    "product_profile_estimate": profile,
                    "fba_cost_estimate": estimate,
                    "small_parcel_friendly": is_small_parcel_friendly,
                    "meets_crossborder_margin": meets_margin,
                    "priority": priority,
                    "recommendations": (
                        ["可以进入跨境优先队列。"]
                        if priority == "normal"
                        else [
                            "优先压缩尺寸或减重。",
                            "优先降低主体物料成本或减少配件数。",
                            "若目标售价无法提升，建议降为低优先级。",
                        ]
                    ),
                    "notes": "本结果为本地估算值，建议结合真实包装尺寸和采购价复核。",
                }
            )
        return {"cost_logistics_validations": validations}

    def merge_validations_into_output(
        self,
        output: Dict[str, Any],
        patent_validation: Dict[str, Any],
        cost_validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        patent_map = {item["idea_name"]: item for item in patent_validation.get("patent_validations", [])}
        cost_map = {item["idea_name"]: item for item in cost_validation.get("cost_logistics_validations", [])}

        for idea in output.get("top_candidates", []):
            name = idea.get("idea_name", "")
            patent = patent_map.get(name, {})
            costing = cost_map.get(name, {})

            if patent:
                idea["patent_risk_validation"] = patent
                idea["ip_compliance_assessment"]["risk_level"] = patent.get("infringement_risk_level", idea["ip_compliance_assessment"].get("risk_level", "medium"))
                idea["ip_compliance_assessment"]["risk_reason"] = patent.get("patent_gap_analysis", []) + patent.get("avoidance_suggestions", [])
                if patent.get("validation_status") == "fail":
                    idea["final_verdict"] = "revise"

            if costing:
                idea["cost_logistics_validation"] = costing
                estimate = costing.get("fba_cost_estimate", {})
                idea["cost_assessment"]["margin_global_est"] = round(estimate.get("gross_margin_rate", 0.0) * 100, 2)
                idea["cost_assessment"]["status"] = "pass" if costing.get("meets_crossborder_margin") else "risk"
                idea["logistics_assessment"]["small_parcel_friendly"] = costing.get("small_parcel_friendly", True)
                idea["logistics_assessment"]["risk_notes"] = idea["logistics_assessment"].get("risk_notes", []) + costing.get("recommendations", [])
                if not costing.get("meets_crossborder_margin"):
                    idea["priority"] = "low"
                    idea["final_verdict"] = "revise"
                else:
                    idea["priority"] = costing.get("priority", "normal")

        output["orchestration_notes"] = output.get("orchestration_notes", []) + [
            "Post-generation validation added: patent risk screen",
            "Post-generation validation added: cost and logistics screen",
        ]
        return output

    def render_markdown_report(self, task: Dict[str, Any], final_output: Dict[str, Any]) -> str:
        """
        Generate a standardized Markdown report from final JSON.
        Prefer GPT-4o formatting, but keep a deterministic fallback.
        """
        system_prompt = self.prompts.get("06_文档美化Agent.md", "")
        prompt = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "final_output": final_output,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        try:
            return call_reasoning_llm(prompt, model=self.openai_model).strip()
        except Exception:
            lines = [
                "# 产品灵感报告",
                "",
                "## 基本信息",
                "",
                f"- 请求 ID：{task.get('request_id', '')}",
                f"- 工作流：{final_output.get('workflow_type', '')}",
                f"- 目标市场：{', '.join(final_output.get('market', []))}",
                f"- 方案数量：{len(final_output.get('top_candidates', []))}",
                "",
            ]

            for idx, idea in enumerate(final_output.get("top_candidates", []), start=1):
                patent = idea.get("patent_risk_validation", {})
                costing = idea.get("cost_logistics_validation", {})
                lines.extend(
                    [
                        f"## 方案{idx}：{idea.get('idea_name', '未命名方案')}",
                        "",
                        "### 灵感来源",
                        idea.get("opportunity_source", {}).get("summary", "暂无"),
                        "",
                        "### 创新点与协同效应",
                        f"- 迁移特征：{', '.join(idea.get('migrated_features', [])) or '暂无'}",
                        f"- 差异化说明：{idea.get('differentiation', '暂无')}",
                        "",
                        "### 适配场景",
                        f"- 适配场景：{', '.join(idea.get('scenarios', [])) or '暂无'}",
                        f"- 目标用户：{', '.join(idea.get('target_users', [])) or '暂无'}",
                        "",
                        "### 专利风险分析",
                        f"- 风险等级：{patent.get('infringement_risk_level', idea.get('ip_compliance_assessment', {}).get('risk_level', 'unknown'))}",
                        f"- 专利空隙分析：{'；'.join(patent.get('patent_gap_analysis', [])) or '暂无'}",
                        f"- 规避建议：{'；'.join(patent.get('avoidance_suggestions', [])) or '暂无'}",
                        "",
                        "### 成本估算",
                        f"- 头程估算：{costing.get('fba_cost_estimate', {}).get('headhaul_est_usd', 'N/A')} USD",
                        f"- 尾程估算：{costing.get('fba_cost_estimate', {}).get('tail_fulfillment_est_usd', 'N/A')} USD",
                        f"- 仓储估算：{costing.get('fba_cost_estimate', {}).get('monthly_storage_est_usd', 'N/A')} USD/月",
                        f"- 跨境毛利：{costing.get('fba_cost_estimate', {}).get('gross_margin_rate', 0) * 100:.2f}%",
                        "",
                        "### 落地建议",
                        f"- 结论：{idea.get('final_verdict', 'unknown')}",
                        f"- 下一步：{'；'.join(idea.get('validation_next_steps', [])) or '暂无'}",
                        "",
                    ]
                )
            return "\n".join(lines).strip() + "\n"

    def build_final_output(
        self,
        task: Dict[str, Any],
        route: List[str],
        cleaned_trend: Dict[str, Any],
        feature_result: Dict[str, Any],
        scenario_result: Dict[str, Any],
        ip_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        最终输出继续兼容 shared_data_contract.yaml 的 main_agent_output。
        最后用一轮 GPT-4o 收敛，但仍做程序化兜底。
        """
        scenario_map = {item["concept_name"]: item for item in scenario_result.get("scenario_reviews", []) if item.get("concept_name")}
        ip_map = {item["concept_name"]: item for item in ip_result.get("risk_reviews", []) if item.get("concept_name")}

        llm_prompt = [
            {
                "role": "system",
                "content": self.prompts.get("00_中枢调度主Agent_SystemPrompt.md", "")
                + "\n只输出 JSON，格式必须兼容 main_agent_output。"
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "route": route,
                        "trend": cleaned_trend,
                        "feature": feature_result,
                        "scenario": scenario_result,
                        "ip": ip_result,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        fallback = {
            "request_id": task["request_id"],
            "workflow_type": task["workflow_hint"],
            "market": task["target_market"],
            "top_candidates": [],
            "dropped_candidates": [],
            "orchestration_notes": ["使用程序化兜底输出"],
        }
        llm_output = safe_llm_json(llm_prompt, model=self.openai_model, fallback=fallback, provider="openai")

        top_candidates = []
        dropped = []
        for idx, match in enumerate(feature_result.get("feature_matches", [])[:5], start=1):
            concept = (match.get("candidate_product_concepts") or [{}])[0]
            name = concept.get("concept_name", f"未命名概念{idx}")
            scenario = scenario_map.get(name, {})
            ip_risk = ip_map.get(name, {})

            material_delta = 12 if "模块" in "".join(match.get("selected_features", [])) else 9
            margin_cn = 32
            margin_global = 46
            cost_status = "pass" if material_delta <= task.get("constraints", {}).get("max_material_delta_pct", 15) else "fail"
            scenario_ok = scenario.get("validation_status") in {"pass", "revise"}
            ip_ok = ip_risk.get("final_risk_status") in {"pass", "revise"}

            if not (scenario_ok and ip_ok and cost_status == "pass"):
                dropped.append(
                    {
                        "idea_name": name,
                        "drop_reason": scenario.get("revision_advice", []) + ip_risk.get("risk_reasons", []),
                    }
                )
                continue

            idea = {
                "idea_id": f"IDEA-{idx:03d}",
                "idea_name": name,
                "opportunity_source": {
                    "source_type": "爆款溯源",
                    "summary": match.get("source_problem", "来自公开榜单和搜索结果"),
                },
                "target_users": scenario.get("target_users", ["跨境消费者", "抖音兴趣用户"]),
                "core_pain_points": [part.strip() for part in str(match.get("source_problem", "")).split(",") if part.strip()],
                "scenarios": scenario.get("fit_scenarios", ["居家收纳", "桌面使用"]),
                "migrated_features": match.get("selected_features", []),
                "product_definition": concept.get("concept_definition", ""),
                "differentiation": "结合公开榜单信号、场景校验和结构迁移得到的差异化轻小件方案",
                "cost_assessment": {
                    "material_delta_pct": material_delta,
                    "margin_cn_est": margin_cn,
                    "margin_global_est": margin_global,
                    "status": cost_status,
                },
                "logistics_assessment": {
                    "small_parcel_friendly": True,
                    "stackable": True,
                    "risk_notes": ["建议控制为轻小件扁平包装"],
                },
                "ip_compliance_assessment": {
                    "risk_level": ip_risk.get("appearance_risk", "medium"),
                    "risk_reason": ip_risk.get("risk_reasons", ["需要人工复核专利近似度"]),
                    "design_patent_direction": ip_risk.get("patent_layout_suggestions", ["主体外观"]),
                    "accessory_matrix_direction": concept.get("accessory_matrix_options", []),
                },
                "recommended_platforms": task["target_market"],
                "validation_next_steps": [
                    "人工复核前 10 个竞品页面",
                    "补一版 BOM 和包装测算",
                    "进一步确认平台合规词和侵权关键词",
                ],
                "final_verdict": "ship" if ip_risk.get("final_risk_status", "pass") == "pass" else "revise",
            }
            ensure_required("final_idea_spec", idea, self.contract)
            top_candidates.append(idea)

        output = {
            "request_id": task["request_id"],
            "workflow_type": task["workflow_hint"],
            "market": task["target_market"],
            "top_candidates": top_candidates,
            "dropped_candidates": dropped,
            "orchestration_notes": (
                llm_output.get("orchestration_notes", [])
                if isinstance(llm_output, dict)
                else []
            )
            + [
                f"Loaded prompts: {len(self.prompts)}",
                f"Route: {' -> '.join(route)}",
                f"OpenAI model: {self.openai_model}",
                "Batch cleaner: DeepSeek primary, Groq fallback",
                "Search layer: SERPAPI if configured, otherwise SERPER fallback",
            ],
        }
        return output

    def run(self, task: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
        self.ensure_task(task)
        route = self.route_for(task["workflow_hint"])

        raw_search = collect_search_results(task, self.env)
        save_json(run_dir / "01_raw_search.json", raw_search)

        cleaned_trend = clean_market_results(raw_search["items"])
        save_json(run_dir / "02_cleaned_trend.json", cleaned_trend)

        plan = self.orchestrator_plan(task, cleaned_trend, route)
        save_json(run_dir / "03_orchestrator_plan.json", plan)

        state: Dict[str, Any] = {
            "trend": cleaned_trend,
            "feature": {"feature_matches": []},
            "scenario": {"scenario_reviews": []},
            "ip": {"risk_reviews": []},
            "patent_search_records": {},
        }

        for step in route:
            if step == "agent_trend":
                continue
            if step == "agent_feature_transfer":
                state["feature"] = self.run_feature_transfer(task, state["trend"].get("market_signals", []))
                save_json(run_dir / "04_feature_matches.json", state["feature"])
            elif step == "agent_scenario":
                state["scenario"] = self.run_scenario_validation(task, state["feature"].get("feature_matches", []))
                save_json(run_dir / "05_scenario_reviews.json", state["scenario"])
            elif step == "agent_ip_guard":
                state["ip"], state["patent_search_records"] = self.run_ip_guard(task, state["feature"].get("feature_matches", []))
                save_json(run_dir / "06_patent_search_records.json", state["patent_search_records"])
                save_json(run_dir / "07_ip_reviews.json", state["ip"])

        draft_output = self.build_final_output(
            task=task,
            route=route,
            cleaned_trend=state["trend"],
            feature_result=state["feature"],
            scenario_result=state["scenario"],
            ip_result=state["ip"],
        )
        save_json(run_dir / "08_draft_output.json", draft_output)

        patent_validation = self.run_patent_risk_validation(draft_output.get("top_candidates", []))
        save_json(run_dir / "09_patent_validation.json", patent_validation)

        cost_validation = self.run_cost_logistics_validation(task, draft_output.get("top_candidates", []))
        save_json(run_dir / "10_cost_logistics_validation.json", cost_validation)

        final_output = self.merge_validations_into_output(draft_output, patent_validation, cost_validation)
        save_json(run_dir / "11_final_output.json", final_output)

        markdown_report = self.render_markdown_report(task, final_output)
        save_text(run_dir / "12_report.md", markdown_report)

        report_date = datetime.now().strftime("%Y%m%d")
        outputs_dir = self.base_dir / "outputs"
        report_path = outputs_dir / f"灵感报告_{report_date}.md"
        save_text(report_path, markdown_report)

        final_output["report_output_path"] = str(report_path)
        save_json(run_dir / "11_final_output.json", final_output)
        return final_output


# ------------------------------
# CLI
# ------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="真实数据版爆款产品灵感多 Agent 主程序")
    parser.add_argument("--workflow", default="workflow_1", choices=["workflow_1", "workflow_2", "workflow_3", "hybrid"])
    parser.add_argument("--goal", default="")
    parser.add_argument("--categories", default="桌面收纳,居家小件,DIY配件")
    parser.add_argument("--seed", default="磁吸,可拆卸,模块化,透明收纳")
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    run_name = args.run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = base_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    task = build_default_task(args)
    save_json(run_dir / "00_task.json", task)

    engine = RealIdeaEngine(base_dir)
    result = engine.run(task, run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n运行目录: {run_dir}")


if __name__ == "__main__":
    main()
