#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
本地单机版多 Agent 轻量模拟脚本

设计目标：
1. 读取当前目录下 prompts/ 的全部 Markdown 提示词
2. 读取 schemas/shared_data_contract.yaml 与 config/agent_registry.yaml
3. 不依赖外部 API、数据库、爬虫，只做模拟数据运行与规则校验
4. 用单文件完成中枢调度、工作流执行、结果终审和 JSON 输出

运行示例：
python local_agent_simulator.py
python local_agent_simulator.py --workflow workflow_2 --goal "找适合亚马逊的桌面收纳新品"
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ------------------------------
# 轻量 YAML 读取
# ------------------------------


def parse_scalar(value: str) -> Any:
    """把 YAML 的基础标量转成 Python 类型。"""
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
    """
    轻量 YAML 解析器。
    这里只覆盖当前项目用到的常见结构：dict、list、缩进嵌套、简单标量。
    如果环境里已经装了 PyYAML，会优先用它；否则走本地解析。
    """
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

                # 形如 "- id: agent_trend" 的行内字典项
                if item_text and ":" in item_text and not item_text.startswith(("http://", "https://")):
                    key, value = item_text.split(":", 1)
                    item: Dict[str, Any] = {key.strip(): parse_scalar(value.strip()) if value.strip() else None}
                    while index < len(raw_lines) and raw_lines[index][0] > indent:
                        child_indent, child_content = raw_lines[index]
                        if child_content.startswith("- "):
                            nested, index = parse_block(index, child_indent)
                            # 这类结构在当前文件里不会作为匿名列表挂到 item 上，保守处理
                            item.setdefault("_items", nested)
                            continue
                        child_key, child_value = child_content.split(":", 1)
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

                if item_text:
                    result.append(parse_scalar(item_text))
                    while index < len(raw_lines) and raw_lines[index][0] > indent:
                        _, child_content = raw_lines[index]
                        if ":" in child_content:
                            # 兼容 "- key" 之后补充子字段的极少数写法
                            extra: Dict[str, Any] = {"value": result.pop()}
                            while index < len(raw_lines) and raw_lines[index][0] > indent:
                                ck, cv = raw_lines[index][1].split(":", 1)
                                ck = ck.strip()
                                cv = cv.strip()
                                if cv:
                                    extra[ck] = parse_scalar(cv)
                                    index += 1
                                else:
                                    nested, index = parse_block(index + 1, raw_lines[index + 1][0])
                                    extra[ck] = nested
                            result.append(extra)
                        else:
                            index += 1
                    continue

                nested, index = parse_block(index, indent + 2)
                result.append(nested)
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

            if index + 1 >= len(raw_lines) or raw_lines[index + 1][0] <= indent:
                result_dict[key] = {}
                index += 1
                continue

            nested, index = parse_block(index + 1, raw_lines[index + 1][0])
            result_dict[key] = nested
        return result_dict, index

    parsed, _ = parse_block(0, raw_lines[0][0] if raw_lines else 0)
    return parsed


# ------------------------------
# 文件读取与元数据
# ------------------------------


def load_prompts(prompts_dir: Path) -> Dict[str, Dict[str, str]]:
    """读取 prompts 目录下全部 Markdown，并抽取标题与正文摘要。"""
    prompts: Dict[str, Dict[str, str]] = {}
    for path in sorted(prompts_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
        body = next((line for line in lines if not line.startswith("#")), "")
        prompts[path.name] = {
            "title": title,
            "summary": body[:120],
            "content": text,
        }
    return prompts


def ensure_required(name: str, payload: Dict[str, Any], contract: Dict[str, Any]) -> None:
    """按 shared_data_contract.yaml 中的 required 字段做最小校验。"""
    obj_def = contract.get("objects", {}).get(name, {})
    required = obj_def.get("required", [])
    missing = [field for field in required if field not in payload or payload[field] in (None, "", [])]
    if missing:
        raise ValueError(f"{name} 缺少必填字段: {', '.join(missing)}")


# ------------------------------
# 模拟数据生成
# ------------------------------


def pick_seed_words(task: Dict[str, Any]) -> List[str]:
    words = list(task.get("seed_keywords") or [])
    if not words:
        words = ["磁吸", "可拆卸", "模块化", "透明收纳"]
    return words


def trend_agent(task: Dict[str, Any]) -> Dict[str, Any]:
    """模拟爆款挖掘：根据品类和关键词生成增长信号。"""
    words = pick_seed_words(task)
    categories = task.get("constraints", {}).get("categories") or ["桌面收纳", "居家小件"]
    signals = []
    for idx, category in enumerate(categories[:2], start=1):
        feature = words[(idx - 1) % len(words)]
        signals.append(
            {
                "signal_id": f"SIG-{idx:03d}",
                "product_name": f"{feature}{category}爆款变体",
                "platform": "douyin_cn" if idx == 1 else "amazon_global",
                "category": category,
                "growth_window": "7d",
                "growth_reason_type": "功能刚需" if "收纳" in category else "小众圈层",
                "target_users": ["独居人群", "Z世代"],
                "user_pain_points": ["收纳散乱", "取用步骤多"],
                "review_complaints": ["不好整理", "占空间", "缺少分区"],
                "premium_drivers": ["结构更省空间", "桌面更整洁"],
                "structure_highlights": [feature, "扁平化包装"],
                "appearance_highlights": ["透明可视", "轻量化"],
                "extractable_features": [feature, "模块化", "分区收纳"],
                "risks": ["同质化风险需进一步校验"],
            }
        )
    return {"agent": "trend_reverse_engineering", "market_signals": signals, "summary": ["已生成模拟爆款信号"]}


def new_domain_agent(task: Dict[str, Any]) -> Dict[str, Any]:
    """模拟新领域探测：给 hybrid / workflow_2 提供增量机会。"""
    words = pick_seed_words(task)
    feature = words[0]
    opportunities = [
        {
            "opportunity_id": "OPP-001",
            "domain_name": f"{feature}桌搭DIY圈层",
            "trend_stage": "早期商业化",
            "signal_sources": ["小红书", "B站", "二手平台"],
            "target_users": ["桌搭爱好者", "手作人群"],
            "behavior_chain": ["收集材料", "摆放", "分类", "展示", "收纳"],
            "explicit_pain_points": ["分类慢", "容易刮花", "展示凌乱"],
            "implicit_pain_points": ["桌面视觉噪音高", "配件丢失"],
            "existing_supply_gap": "有内容热度，但专用工具少",
            "commercialization_hypothesis": ["适合做轻小件工具化产品", "适合延伸配件矩阵"],
            "recommended_followup_queries": ["跨赛道找相似收纳结构", "验证亚马逊轻小件可行性"],
        }
    ]
    return {"agent": "new_domain_detection", "opportunities": opportunities, "summary": ["已生成模拟新领域机会"]}


def scenario_agent(concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """模拟场景校验：过滤显著不成立的方案。"""
    reviews = []
    for idx, concept in enumerate(concepts, start=1):
        text = " ".join(concept.get("selected_features", []))
        risky = "带灯" in text or "激光" in text
        status = "fail" if risky else "pass"
        reviews.append(
            {
                "review_id": f"SCN-{idx:03d}",
                "concept_name": concept["candidate_product_concepts"][0]["concept_name"],
                "target_users": ["独居人群", "桌搭用户"],
                "usage_chain": ["拿取", "摆放", "固定", "收纳"],
                "fit_scenarios": ["居家收纳", "桌面使用"],
                "conflict_scenarios": ["车载暴晒"] if risky else [],
                "explicit_needs": ["好整理", "易拿取"],
                "implicit_needs": ["桌面更整洁", "减少丢件"],
                "storage_requirements": ["可堆叠", "扁平化"],
                "environment_constraints": ["避免高温暴晒"] if risky else ["常温室内"],
                "validation_status": status,
                "revision_advice": ["移除高风险带电或灯光元素"] if risky else ["保持轻小件结构"],
            }
        )
    return {"agent": "scenario_validation", "scenario_reviews": reviews, "summary": ["已完成场景模拟校验"]}


def feature_transfer_agent(task: Dict[str, Any], upstream: Dict[str, Any]) -> Dict[str, Any]:
    """模拟特征迁移：把上游信号转成候选产品定义。"""
    seed_words = pick_seed_words(task)
    candidates = []

    source_items = upstream.get("market_signals") or upstream.get("opportunities") or []
    if not source_items:
        source_items = [{"product_name": "基础收纳组件", "user_pain_points": ["收纳慢"], "extractable_features": seed_words}]

    for idx, item in enumerate(source_items[:3], start=1):
        source_problem = (
            ", ".join(item.get("user_pain_points", []))
            or ", ".join(item.get("explicit_pain_points", []))
            or "收纳和分类效率低"
        )
        abstracted = item.get("extractable_features") or seed_words[:3]
        selected = abstracted[:2] if len(abstracted) >= 2 else abstracted
        concept_name = f"{selected[0]}多分区收纳板{idx}"
        candidates.append(
            {
                "match_id": f"MAT-{idx:03d}",
                "source_problem": source_problem,
                "abstracted_features": abstracted,
                "selected_features": selected,
                "rejected_features": ["带灯装饰", "复杂电动结构"],
                "feature_selection_reason": ["优先解决分类和收纳痛点", "优先轻量低成本结构"],
                "candidate_product_concepts": [
                    {
                        "concept_name": concept_name,
                        "concept_definition": f"面向桌面收纳场景的{selected[0]}+{selected[-1]}轻小件工具",
                        "structural_points": ["可拆分分区", "透明上盖", "扁平底座"],
                        "interaction_points": ["快速拿取", "分区归类"],
                        "accessory_matrix_options": ["补充分隔件", "防尘盖", "挂架配件"],
                        "cost_risk_notes": ["控制配件数量", "优先通用塑件"],
                        "compliance_risk_notes": ["避免带电", "避免尖锐结构"],
                    }
                ],
            }
        )
    return {"agent": "feature_transfer_engine", "feature_matches": candidates, "summary": ["已生成候选概念"]}


def ip_guard_agent(concepts: List[Dict[str, Any]], task: Dict[str, Any]) -> Dict[str, Any]:
    """模拟专利与竞品预警。"""
    reviews = []
    avoid_dense = bool(task.get("constraints", {}).get("avoid_patent_dense_shapes", True))
    for idx, concept in enumerate(concepts, start=1):
        item = concept["candidate_product_concepts"][0]
        name = item["concept_name"]
        dense_shape = "球" in name or "熊" in name
        risk = "high" if dense_shape and avoid_dense else "low"
        reviews.append(
            {
                "risk_id": f"IPR-{idx:03d}",
                "concept_name": name,
                "appearance_risk": risk,
                "structure_risk": "low",
                "platform_compliance_risk": "low",
                "competitor_overlap_risk": "medium" if "磁吸" in name else "low",
                "risk_reasons": ["结构较常规，需做差异化外观"] if risk != "high" else ["外观形态落入高密集专利区"],
                "safe_zones": ["分区结构", "通用配件接口"],
                "redesign_directions": ["增加模块接口差异", "避免热门轮廓形状"],
                "patent_layout_suggestions": ["主体外观", "分隔件", "防尘盖", "挂架配件"],
                "final_risk_status": "fail" if risk == "high" else "pass",
            }
        )
    return {"agent": "ip_risk_guard", "risk_reviews": reviews, "summary": ["已完成专利与竞品模拟预警"]}


# ------------------------------
# 中枢 Agent 编排
# ------------------------------


class LocalAgentSimulator:
    """主调度器：读取配置、执行工作流、合并结果。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.prompts = load_prompts(base_dir / "prompts")
        self.contract = load_yaml_like(base_dir / "schemas" / "shared_data_contract.yaml")
        self.registry = load_yaml_like(base_dir / "config" / "agent_registry.yaml")

    def route_for(self, workflow: str) -> List[str]:
        rules = self.registry.get("routing_rules", {})
        route = rules.get(workflow, {}).get("order", [])
        return route or ["agent_trend", "agent_feature_transfer", "agent_scenario", "agent_ip_guard", "main_agent_final_review"]

    def validate_task(self, task: Dict[str, Any]) -> None:
        ensure_required("task_brief", task, self.contract)

    def run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        self.validate_task(task)
        workflow = task["workflow_hint"]
        route = self.route_for(workflow)

        state: Dict[str, Any] = {
            "task": deepcopy(task),
            "trend": None,
            "new_domain": None,
            "feature": None,
            "scenario": None,
            "ip": None,
        }

        for step in route:
            if step == "agent_trend":
                state["trend"] = trend_agent(task)
            elif step == "agent_new_domain":
                state["new_domain"] = new_domain_agent(task)
            elif step == "agent_feature_transfer":
                upstream = state["trend"] or state["new_domain"] or {}
                state["feature"] = feature_transfer_agent(task, upstream)
            elif step == "agent_scenario":
                concepts = (state["feature"] or {}).get("feature_matches", [])
                state["scenario"] = scenario_agent(concepts)
            elif step == "agent_ip_guard":
                concepts = (state["feature"] or {}).get("feature_matches", [])
                state["ip"] = ip_guard_agent(concepts, task)

        return self.final_review(state, route)

    def final_review(self, state: Dict[str, Any], route: List[str]) -> Dict[str, Any]:
        """按 shared_data_contract 的目标结构输出最终结果。"""
        task = state["task"]
        feature_matches = (state["feature"] or {}).get("feature_matches", [])
        scenario_reviews = {item["concept_name"]: item for item in (state["scenario"] or {}).get("scenario_reviews", [])}
        ip_reviews = {item["concept_name"]: item for item in (state["ip"] or {}).get("risk_reviews", [])}

        top_candidates = []
        dropped_candidates = []

        for idx, match in enumerate(feature_matches, start=1):
            concept = match["candidate_product_concepts"][0]
            name = concept["concept_name"]
            scenario = scenario_reviews.get(name, {})
            ip_risk = ip_reviews.get(name, {})

            material_delta = 12 if "模块化" in " ".join(match.get("selected_features", [])) else 9
            margin_cn = 35
            margin_global = 48
            logistics_ok = True
            cost_ok = material_delta <= task.get("constraints", {}).get("max_material_delta_pct", 15)
            scenario_ok = scenario.get("validation_status") == "pass"
            ip_ok = ip_risk.get("final_risk_status") == "pass"

            if not (cost_ok and logistics_ok and scenario_ok and ip_ok):
                reason = []
                if not cost_ok:
                    reason.append("成本增幅超过约束")
                if not logistics_ok:
                    reason.append("不满足轻小件物流要求")
                if not scenario_ok:
                    reason.extend(scenario.get("revision_advice", ["场景校验未通过"]))
                if not ip_ok:
                    reason.extend(ip_risk.get("risk_reasons", ["专利/竞品风险过高"]))
                dropped_candidates.append({"idea_name": name, "drop_reason": reason})
                continue

            idea = {
                "idea_id": f"IDEA-{idx:03d}",
                "idea_name": name,
                "opportunity_source": {
                    "source_type": "爆款溯源" if state["trend"] else "新领域探测",
                    "summary": match["source_problem"],
                },
                "target_users": scenario.get("target_users", ["独居人群", "桌搭爱好者"]),
                "core_pain_points": [part.strip() for part in match["source_problem"].split(",") if part.strip()],
                "scenarios": scenario.get("fit_scenarios", ["居家收纳"]),
                "migrated_features": match.get("selected_features", []),
                "product_definition": concept["concept_definition"],
                "differentiation": "通过模块化分区和轻小件结构提升整理效率",
                "cost_assessment": {
                    "material_delta_pct": material_delta,
                    "margin_cn_est": margin_cn,
                    "margin_global_est": margin_global,
                    "status": "pass",
                },
                "logistics_assessment": {
                    "small_parcel_friendly": True,
                    "stackable": True,
                    "risk_notes": ["适合扁平化包装"],
                },
                "ip_compliance_assessment": {
                    "risk_level": ip_risk.get("appearance_risk", "low"),
                    "risk_reason": ip_risk.get("risk_reasons", ["未发现高风险项"]),
                    "design_patent_direction": ip_risk.get("patent_layout_suggestions", ["主体外观"]),
                    "accessory_matrix_direction": concept.get("accessory_matrix_options", []),
                },
                "recommended_platforms": task["target_market"],
                "validation_next_steps": [
                    "用 3 个真实竞品做外观差异比对",
                    "补一版包装尺寸与毛利测算",
                    "人工确认是否存在平台限制词",
                ],
                "final_verdict": "ship",
            }
            ensure_required("final_idea_spec", idea, self.contract)
            top_candidates.append(idea)

        output = {
            "request_id": task["request_id"],
            "workflow_type": task["workflow_hint"],
            "market": task["target_market"],
            "top_candidates": top_candidates,
            "dropped_candidates": dropped_candidates,
            "orchestration_notes": [
                f"Loaded prompts: {len(self.prompts)}",
                f"Route: {' -> '.join(route)}",
                f"Main agent: {self.registry.get('main_agent', {}).get('name', 'orchestrator')}",
            ],
        }
        return output


# ------------------------------
# CLI
# ------------------------------


def build_demo_task(args: argparse.Namespace) -> Dict[str, Any]:
    """给脚本提供一个默认可跑的任务输入。"""
    return {
        "request_id": "REQ-LOCAL-001",
        "user_goal": args.goal or "为亚马逊和抖音寻找桌面收纳赛道的可专利新品灵感",
        "target_market": ["douyin_cn", "amazon_global"],
        "workflow_hint": args.workflow,
        "constraints": {
            "categories": ["桌面收纳", "DIY配件", "居家小件"],
            "exclude_categories": ["带电产品", "大件家具"],
            "max_material_delta_pct": 15,
            "min_margin_cn": 30,
            "min_margin_global": 45,
            "small_parcel_only": True,
            "avoid_electronics": True,
            "avoid_patent_dense_shapes": True,
        },
        "seed_keywords": [item.strip() for item in args.seed.split(",") if item.strip()],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="本地多 Agent 轻量模拟器")
    parser.add_argument("--workflow", default="workflow_1", choices=["workflow_1", "workflow_2", "workflow_3", "hybrid"])
    parser.add_argument("--goal", default="")
    parser.add_argument("--seed", default="磁吸,可拆卸,模块化,透明收纳")
    parser.add_argument("--output", default="demo_output.json")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    simulator = LocalAgentSimulator(base_dir)
    task = build_demo_task(args)
    result = simulator.run(task)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = base_dir / output_path
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n输出文件已写入: {output_path}")


if __name__ == "__main__":
    main()
