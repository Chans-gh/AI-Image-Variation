"""最小 JSON Schema（Draft 2020-12 子集）校验器。

为什么自带：本包的测试要在没有第三方依赖的环境也能跑（v1.0 包依赖 jsonschema，
而 jsonschema 4.x 需要编译扩展 rpds；在某些受签名限制的环境无法加载）。

支持的语义：$ref(#/$defs/...)、type、enum、const、required、properties、
additionalProperties(bool)、items、prefixItems、minItems、maxItems、uniqueItems、
minLength、maxLength、pattern、minimum、maximum、exclusiveMinimum、exclusiveMaximum、
multipleOf、allOf、anyOf、oneOf、not、if/then/else。format 不做校验。

不支持的关键字会被忽略——因此本校验器只用于本包 contracts/ 下这 10 份 schema，
它们只使用上述子集。
"""
from __future__ import annotations
import json
import re

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(instance, name) -> bool:
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if name == "boolean":
        return isinstance(instance, bool)
    expected = _TYPES.get(name)
    return expected is not None and isinstance(instance, expected)


def _same(a, b) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _resolve(ref: str, root: dict):
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref supported: {ref}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def validate(instance, schema, root=None, path="$"):
    root = root if root is not None else schema
    errors: list[str] = []

    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        errors += validate(instance, _resolve(schema["$ref"], root), root, path)

    if "type" in schema:
        names = schema["type"]
        names = names if isinstance(names, list) else [names]
        if not any(_type_ok(instance, n) for n in names):
            errors.append(f"{path}: expected type {names}, got {type(instance).__name__}")

    if "enum" in schema:
        if not any(_same(instance, v) for v in schema["enum"]):
            errors.append(f"{path}: {instance!r} not in enum {schema['enum']!r}")

    if "const" in schema:
        if not _same(instance, schema["const"]):
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        for key, subschema in props.items():
            if key in instance:
                errors += validate(instance[key], subschema, root, f"{path}.{key}")
        ap = schema.get("additionalProperties")
        if ap is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: additional property '{key}' not allowed")

    if isinstance(instance, list):
        for i, subschema in enumerate(schema.get("prefixItems", [])):
            if i < len(instance):
                errors += validate(instance[i], subschema, root, f"{path}[{i}]")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors += validate(item, items, root, f"{path}[{i}]")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected >= {schema['minItems']} items, got {len(instance)}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected <= {schema['maxItems']} items, got {len(instance)}")
        if schema.get("uniqueItems"):
            seen = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in instance]
            if len(set(seen)) != len(seen):
                errors.append(f"{path}: items must be unique")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {instance} <= exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {instance} >= exclusiveMaximum {schema['exclusiveMaximum']}")
        if "multipleOf" in schema and schema["multipleOf"]:
            if abs(instance / schema["multipleOf"] - round(instance / schema["multipleOf"])) > 1e-9:
                errors.append(f"{path}: {instance} not multipleOf {schema['multipleOf']}")

    for subschema in schema.get("allOf", []):
        errors += validate(instance, subschema, root, path)

    if "anyOf" in schema:
        if not any(not validate(instance, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: no anyOf branch matched")

    if "oneOf" in schema:
        matched = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path))
        if matched != 1:
            errors.append(f"{path}: expected exactly 1 oneOf branch to match, got {matched}")

    if "not" in schema:
        if not validate(instance, schema["not"], root, path):
            errors.append(f"{path}: matched a forbidden 'not' schema")

    if "if" in schema:
        if not validate(instance, schema["if"], root, path):
            if "then" in schema:
                errors += validate(instance, schema["then"], root, path)
        elif "else" in schema:
            errors += validate(instance, schema["else"], root, path)

    return errors


def check_schema(schema) -> list[str]:
    """对 schema 自身做轻量结构检查（能否被本子集解析）。"""
    errors = []
    if not isinstance(schema, dict):
        return ["schema is not an object"]
    if "$schema" not in schema:
        errors.append("missing $schema")
    return errors
