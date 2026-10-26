"""schema_contract_27b.py — schema 契约比对器（P-053/L4）。

只依赖标准库；不写文件、不打印、不联网；任何异常都转成问题字符串。
"""

from __future__ import annotations

from typing import Any


def _safe_get(obj: Any, key: Any) -> Any:
    """安全取键，失败返回 None。"""
    try:
        if isinstance(obj, dict):
            return obj[key]
    except Exception:
        pass
    return None


def _is_false(value: Any) -> bool:
    """判断 value 是否为 False（严格布尔 False，不接受 0/None 等）。"""
    return value is False


def _check_object_shape(schema: Any, label: str) -> list[str]:
    """检查 schema 是否为 object 且 additionalProperties is False。

    返回问题列表（空 = 通过）。
    """
    problems: list[str] = []
    if not isinstance(schema, dict):
        problems.append(f"{label} 不是对象")
        return problems
    if schema.get("type") != "object":
        problems.append(f"{label} type 不是 object")
    if not _is_false(schema.get("additionalProperties")):
        problems.append(f"{label} additionalProperties 不是 False")
    return problems


def _check_key_set(
    properties: Any,
    expected: set[str],
    missing_prefix: str,
    extra_prefix: str,
) -> list[str]:
    """检查 properties 键集合是否恰好等于 expected。

    缺键报 missing_prefix + key，多键报 extra_prefix + key。
    """
    problems: list[str] = []
    if not isinstance(properties, dict):
        # properties 缺失或不是 dict：所有期望键都算缺
        for key in sorted(expected):
            problems.append(f"{missing_prefix}{key}")
        return problems

    actual_keys = set(properties.keys())
    expected_keys = set(expected)

    for key in sorted(expected_keys - actual_keys):
        problems.append(f"{missing_prefix}{key}")
    for key in sorted(actual_keys - expected_keys):
        problems.append(f"{extra_prefix}{key}")
    return problems


def schema_contract(
    schema: dict,
    *,
    top_level: set[str],
    nested: dict[str, set[str]],
    target_required: set[str],
) -> list[str]:
    """比对 schema 与契约，返回问题列表（空 = 通过）。

    参数:
        schema: 待检查的 JSON Schema 对象。
        top_level: 顶层 properties 必须恰好包含的键集合。
        nested: 嵌套字段名 -> 该字段 properties 必须恰好包含的键集合。
        target_required: schema["properties"]["目标"]["required"] 排序后必须等于 sorted(target_required)。

    返回:
        问题字符串列表；空列表表示通过。
    """
    problems: list[str] = []

    # 1. schema 必须是 dict，且 type == "object"、additionalProperties is False
    if not isinstance(schema, dict):
        return ["schema 不是对象"]

    problems.extend(_check_object_shape(schema, "schema"))

    # 2. schema["properties"] 的键集合必须恰好等于 top_level
    properties = _safe_get(schema, "properties")
    problems.extend(
        _check_key_set(
            properties,
            top_level,
            missing_prefix="缺少顶层键: ",
            extra_prefix="多余顶层键: ",
        )
    )

    # 3. 对 nested 里的每个字段：子 schema 必须存在、type == "object"、
    #    additionalProperties is False，且 properties 键集合恰好等于 nested[field]
    for field in sorted(nested.keys()):
        expected_keys = nested[field]
        sub_schema = _safe_get(properties, field) if isinstance(properties, dict) else None

        if sub_schema is None:
            # 子 schema 不存在：所有期望键都算缺
            for key in sorted(expected_keys):
                problems.append(f"缺少{field}键: {key}")
            continue

        # 子 schema 形状检查
        sub_problems = _check_object_shape(sub_schema, f"{field}")
        # 将子 schema 形状问题也带上字段名，便于定位
        for p in sub_problems:
            problems.append(f"{field}: {p}")

        sub_properties = _safe_get(sub_schema, "properties")
        problems.extend(
            _check_key_set(
                sub_properties,
                expected_keys,
                missing_prefix=f"缺少{field}键: ",
                extra_prefix=f"多余{field}键: ",
            )
        )

    # 4. schema["properties"]["目标"]["required"] 排序后必须等于 sorted(target_required)
    target_schema = _safe_get(properties, "目标") if isinstance(properties, dict) else None
    if target_schema is None:
        problems.append("目标 required 不一致")
    else:
        required = _safe_get(target_schema, "required")
        if not isinstance(required, list):
            problems.append("目标 required 不一致")
        else:
            try:
                actual_sorted = sorted(required)
                expected_sorted = sorted(target_required)
                if actual_sorted != expected_sorted:
                    problems.append("目标 required 不一致")
            except Exception:
                problems.append("目标 required 不一致")

    return problems
