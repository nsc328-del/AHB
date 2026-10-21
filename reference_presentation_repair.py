"""Recover a paid organization response and correct only invalid rows once."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import providers
import reference_presentation as p

SYSTEM = """你修正一份已完成的视频组织分析中的指定字段，不重新分析整片。
输入包含付费原答的错误节点、相邻只读节点、明确校验反馈、对应镜头的实际时间表和既有观察。
没有重新提供图片，不得声称新看到了画面；只据已有观察与真实frame_times校正对齐和命名。
只返回allowed_node_indices/allowed_link_indices指定的记录；其他节点、连接、类型、摘要与依据一律保留。
节点first_shot/last_shot为包含首尾的镜头编号，必须与相邻只读节点无缝覆盖；不能创造或重复镜头。
根据镜头范围对应的observations核对具体场景与论点；如果原节点名称错配了时间，改正该问题节点名称，
不能仅改证据数字让错误描述通过。不是每一个语义段都有独立检测切镜，遇到同镜头跨内容时准确合并描述。
节点描述优先静态场景、姿态或已转写论点。背景屏幕位移不是摄影机物理方向，不新增步行、迈步、相机左右判断。
evidence_times只能取对应shots.frame_times。frame_times为空说明本次没有提供该镜头图片，
此时节点evidence_times可以为空，但仍须用已有观察命名；不能把镜头起止时间或observations里的旧帧时间冒充本次图片。
不得只为通过校验删除错误证据；先确认描述属于哪些镜头，选对应真实证据。相关镜头无本次图片时明确不填新证据。
links的after_node=N表示连接第N与第N+1个节点，每个相邻节点只有一条，不能新增多余链接。
更正后的description需对应两侧实际内容，不能延续原来错位的一整列连接。证据在有frame_times的两侧各取至少一帧；
motion_match两侧各至少两帧。没有直接图像的内容推进可依据已有转写与观察，但不能假造图像引用。
discarded_links逐条确认原答中超出相邻连接数量的多余行并说明原因，不得删除其他已有有效连接。
严格输出JSON，不输出未授权字段。"""


def _path(asset, name, schema, system, context, frames):
    evidence = [{"time": frame["time"], "sha256": hashlib.sha256(Path(frame["path"]).read_bytes()).hexdigest(),
                 **({"label": frame["label"]} if "label" in frame else {})} for frame in frames or []]
    signature = hashlib.sha256(json.dumps({"model": providers.DEEPSEEK_MODEL, "name": name, "schema": schema,
        "system": system, "context": context, "evidence": evidence}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return Path(asset["_analysis_cache_dir"]) / (signature+".response.json"), signature


def _candidate(asset, name, schema, system, context, frames):
    path, signature = _path(asset, name, schema, system, context, frames)
    if not path.is_file():
        return None
    saved = json.loads(path.read_text())
    if saved.get("signature") != signature or saved.get("result", {}).get("provider_status") != "completed":
        return None
    try:
        value = json.loads(saved["result"]["output_text"])
        providers._validate_shape(value, schema)
    except (ValueError, KeyError, TypeError, providers.ProviderError):
        return None
    return value, {"cache_hit": True, "source_job_id": saved.get("source_job_id"), "new_provider_calls": 0}


def diagnostics(value, context):
    shots, nodes = context["shots"], value["nodes"]
    errors, bad_nodes = [], set()
    flattened = [i for n in nodes for i in range(n["first_shot"], n["last_shot"]+1)]
    if flattened != list(range(1, len(shots)+1)):
        raise providers.ProviderError("镜头范围整体不连续，不能局部修补而改变已验证节点")
    def times(node):
        return [t for shot in shots[node["first_shot"]-1:node["last_shot"]] for t in shot["frame_times"]]
    for i, node in enumerate(nodes, 1):
        allowed = times(node)
        invalid = [t for t in node["evidence_times"] if not p._has_time(allowed, t)]
        motion = p._node_motion_error(node, context)
        if invalid or (allowed and not node["evidence_times"]) or motion:
            bad_nodes.add(i)
            errors.append({"node": i, "invalid_evidence_times": invalid, "allowed_frame_times": allowed,
                           "motion_claim_needs_revision": motion,
                           "instruction": "核对场景与镜头范围；修正时间错配，不能仅删除错误引用。"})
    bad_links = set()
    for i in range(1, len(nodes)):
        if i > len(value["links"]):
            bad_links.add(i)
            continue
        link = value["links"][i-1]
        groups = [times(node) for node in nodes[i-1:i+1]]
        invalid = [t for t in link["evidence_times"] if not p._has_time(groups[0]+groups[1], t)]
        insufficient = any(group and sum(p._has_time(group, t) for t in link["evidence_times"]) <
                           (2 if link["kind"] == "motion_match" else 1) for group in groups)
        # A changed endpoint invalidates its neighboring connection, even if a
        # syntactically valid old frame happens to overlap that pair.
        if invalid or insufficient or i in bad_nodes or i+1 in bad_nodes:
            bad_links.add(i)
            errors.append({"after_node": i, "invalid_evidence_times": invalid,
                           "endpoint_changed": i in bad_nodes or i+1 in bad_nodes,
                           "instruction": "按更正后的两个相邻节点重核对应内容与证据。"})
    extras = list(range(len(nodes), len(value["links"])+1))
    if extras:
        errors.append({"extra_original_link_indices": extras, "required_link_count": len(nodes)-1})
    if not bad_nodes and not bad_links and not extras:
        raise providers.ProviderError("组织分析未通过，但没有可安全局部纠正的字段")
    return sorted(bad_nodes), sorted(bad_links), extras, errors


def _repair_schema(count):
    node = copy.deepcopy(p._schema(count)["properties"]["nodes"]["items"])
    node["properties"]["index"] = {"type": "integer", "minimum": 1}
    node["required"].append("index")
    link = copy.deepcopy(p._schema(count)["properties"]["links"]["items"])
    link["properties"]["after_node"] = {"type": "integer", "minimum": 1}
    link["required"].append("after_node")
    return providers._object({"nodes": providers._array(node), "links": providers._array(link),
        "discarded_links": providers._array(providers._object({"index": {"type": "integer"}, "reason": providers._string()}))})


def _correct(asset, original, context, original_receipt):
    bad_nodes, bad_links, extras, errors = diagnostics(original, context)
    needed = set(bad_nodes)
    for index in bad_links:
        needed.update((index, index+1))
    for index in bad_nodes:
        needed.update(i for i in (index-1, index+1) if 1 <= i <= len(original["nodes"]))
    relevant_shots = set()
    for index in needed:
        node = original["nodes"][index-1]
        relevant_shots.update(range(node["first_shot"], node["last_shot"]+1))
    shot_rows = [s for s in context["shots"] if s["index"] in relevant_shots]
    observation_ids = {oid for s in shot_rows for oid in s["observation_ids"]}
    observations = [{key: row[key] for key in ("id", "first_shot", "last_shot", "start", "end", "description", "speech_evidence") if key in row}
                    for row in context["observations"] if row["id"] in observation_ids]
    correction_context = {
        "shot_count": len(context["shots"]), "node_count": len(original["nodes"]),
        "allowed_node_indices": bad_nodes, "allowed_link_indices": bad_links,
        "required_discarded_link_indices": extras, "validation_feedback": errors,
        "nodes": [{"index": i, "read_only": i not in bad_nodes, **original["nodes"][i-1]} for i in sorted(needed)],
        "links": [{"original_index": i, **original["links"][i-1]} for i in sorted(set(bad_links+extras)) if i <= len(original["links"])],
        "shots": [{k: s[k] for k in ("index", "start", "end", "frame_times", "observation_ids")} for s in shot_rows],
        "observations": observations,
    }
    schema = _repair_schema(len(context["shots"]))
    def apply_patch(patch):
        if (sorted(n["index"] for n in patch["nodes"]) != bad_nodes or
            sorted(l["after_node"] for l in patch["links"]) != bad_links or
            sorted(l["index"] for l in patch["discarded_links"]) != extras):
            raise providers.ProviderError("局部纠正改变了未授权节点、连接或遗漏问题字段")
        result = copy.deepcopy(original)
        for node in patch["nodes"]:
            result["nodes"][node["index"]-1] = {key: item for key, item in node.items() if key != "index"}
        replacements = {link["after_node"]: {key: item for key, item in link.items() if key != "after_node"} for link in patch["links"]}
        result["links"] = [replacements.get(i, copy.deepcopy(original["links"][i-1]) if i <= len(original["links"]) else None)
                           for i in range(1, len(original["nodes"]))]
        p._validate(result, context)
        return result
    cached = _candidate(asset, "reference_presentation_correction", schema, SYSTEM, correction_context, None)
    if cached:
        corrected, receipt = cached
        apply_patch(corrected)
    else:
        path, _ = _path(asset, "reference_presentation_correction", schema, SYSTEM, correction_context, None)
        if path.exists():
            raise providers.ProviderError("本次局部纠正已尝试一次但未通过，原分析和回复均已保留")
        corrected, receipt = providers._analysis_response(
            asset, "reference_presentation_correction", schema, SYSTEM, correction_context, None, apply_patch)
    result = apply_patch(corrected)
    new_calls = original_receipt.get("new_provider_calls", int(not original_receipt["cache_hit"])) + int(not receipt["cache_hit"])
    return result, {**receipt, "cache_hit": new_calls == 0, "new_provider_calls": new_calls,
                    "correction": {"original_source_job_id": original_receipt["source_job_id"],
                        "source_job_id": receipt["source_job_id"], "node_indices": bad_nodes,
                        "link_indices": bad_links, "discarded_links": corrected["discarded_links"],
                        "max_attempts": 1, "images_resent": 0}}


def _accept(value, receipt, context):
    p._validate_classification(value, context)
    if value["kind"] != "narrative":
        p._validate(value, context)
        return value, receipt
    # The top-level organization finding and the model's proposed timeline are
    # independent claims. Preserve the finding, never publish failed/unused rows.
    receipt = {**receipt, "classification_validated_separately": True}
    if value.get("nodes") or value.get("links"):
        audit = {"source_job_id": receipt["source_job_id"],
                 "raw_response_sha256": hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                 "unpublished_node_count": len(value.get("nodes", [])),
                 "unpublished_link_count": len(value.get("links", [])),
                 "reason": "narrative_uses_existing_content_units_not_model_shot_ranges"}
        try:
            invalid_nodes, affected_links, extras, _ = diagnostics(value, context)
            audit.update(invalid_node_indices=invalid_nodes, affected_link_indices=affected_links,
                         surplus_link_indices=extras, model_rows_validated=False)
        except providers.ProviderError:
            audit["model_rows_validated"] = False
        receipt["unpublished_response_audit"] = audit
    # Return only the independently checked classification; projection occurs
    # against the exact immutable content source in the workflow.
    return {**{key: copy.deepcopy(value[key]) for key in (
        "kind", "label", "summary", "confidence", "basis", "limitations")}, "nodes": [], "links": []}, receipt


def response(asset, context, frames):
    schema = p._schema(len(context["shots"]))
    # The v2 fingerprint stays readable so already paid classification results
    # remain reusable after the semantic-unit contract is corrected.
    legacy = _candidate(asset, "reference_presentation", schema, p.SYSTEM, context, frames)
    if legacy:
        value, receipt = legacy
        try:
            return _accept(value, receipt, context)
        except providers.ProviderError:
            if value["kind"] == "narrative":
                raise
            return _correct(asset, value, context, receipt)
    name, system = "reference_organization", p.ORGANIZATION_SYSTEM
    candidate = _candidate(asset, name, schema, system, context, frames)
    if candidate:
        value, receipt = candidate
        try:
            return _accept(value, receipt, context)
        except providers.ProviderError:
            if value["kind"] == "narrative":
                raise
            return _correct(asset, value, context, receipt)
    try:
        value, receipt = providers._analysis_response(asset, name, schema, system, context, frames,
            lambda value: _accept(value, {"source_job_id": None}, context))
        return _accept(value, receipt, context)
    except providers.ProviderError:
        candidate = _candidate(asset, name, schema, system, context, frames)
        if not candidate:
            raise
        value, receipt = candidate
        receipt.update(cache_hit=False, new_provider_calls=1)
        if value["kind"] == "narrative":
            return _accept(value, receipt, context)
        return _correct(asset, value, context, receipt)
