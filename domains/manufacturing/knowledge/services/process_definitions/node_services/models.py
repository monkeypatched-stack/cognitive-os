from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from typing import Any
import xml.etree.ElementTree as ET


@dataclass
class NodeExecutionResult:
    ok: bool
    status_code: int | None
    response_body: Any
    error: str | None = None


def config_value(config: dict, key: str, default=None):
    value = config.get(key)
    return default if value is None or value == "" else value


def render_template(template: str, payload: dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return template.format(**payload)
    except Exception:
        return template


def payload_message(
    payload: dict[str, Any], config: dict, template_key: str = "message_template"
) -> str:
    template = config.get(template_key)
    if isinstance(template, str) and template.strip():
        try:
            return template.format(**payload)
        except Exception:
            return template
    if "message" in payload:
        return str(payload["message"])
    if "payload" in payload:
        value = payload["payload"]
        return value if isinstance(value, str) else json.dumps(value)
    return json.dumps(payload)


def nested_value(value: Any, path: str):
    current = value
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def set_nested_value(
    value: dict[str, Any], path: str, new_value: Any
) -> dict[str, Any]:
    if not path:
        return value
    parts = [part for part in path.split(".") if part]
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if parts:
        current[parts[-1]] = new_value
    return value


def delete_nested_value(value: dict[str, Any], path: str) -> dict[str, Any]:
    parts = [part for part in path.split(".") if part]
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return value
        current = child
    if parts:
        current.pop(parts[-1], None)
    return value


def coerce_config_value(value: Any, value_type: str | None) -> Any:
    value_type = (value_type or "json").lower()
    if value_type == "string":
        return "" if value is None else str(value)
    if value_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def rule_matches(actual: Any, rule: dict) -> bool:
    operation = str(rule.get("operation") or rule.get("op") or "eq").lower()
    expected = rule.get("value")
    if operation in {"exists", "present"}:
        return actual is not None
    if operation in {"missing", "not_exists"}:
        return actual is None
    if operation in {"eq", "equals", "=="}:
        return actual == expected
    if operation in {"neq", "not_equals", "!="}:
        return actual != expected
    if operation == "contains":
        return str(expected) in str(actual)
    try:
        actual_num = float(actual)
        expected_num = float(expected)
    except (TypeError, ValueError):
        return False
    if operation in {"gt", ">"}:
        return actual_num > expected_num
    if operation in {"gte", ">="}:
        return actual_num >= expected_num
    if operation in {"lt", "<"}:
        return actual_num < expected_num
    if operation in {"lte", "<="}:
        return actual_num <= expected_num
    return False


class SimpleHTMLExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.text.append(stripped)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def xml_to_dict(element: ET.Element) -> dict:
    children = list(element)
    attrs = {f"@{key}": value for key, value in element.attrib.items()}
    if not children:
        text = (element.text or "").strip()
        return {element.tag: {**attrs, "#text": text} if attrs else text}
    body: dict[str, Any] = dict(attrs)
    for child in children:
        child_dict = xml_to_dict(child)
        for key, value in child_dict.items():
            if key in body:
                if not isinstance(body[key], list):
                    body[key] = [body[key]]
                body[key].append(value)
            else:
                body[key] = value
    return {element.tag: body}


def dict_to_xml(name: str, value: Any) -> ET.Element:
    element = ET.Element(str(name))
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("@"):
                element.set(str(key)[1:], str(item))
            elif key == "#text":
                element.text = str(item)
            elif isinstance(item, list):
                for child in item:
                    element.append(dict_to_xml(str(key), child))
            else:
                element.append(dict_to_xml(str(key), item))
    else:
        element.text = "" if value is None else str(value)
    return element


def simple_yaml_parse(text: str) -> dict:
    parsed: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        parsed[key.strip()] = coerce_config_value(value.strip(), "json")
    return parsed


def simple_yaml_dump(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(
            f"{key}: {json.dumps(item) if isinstance(item, (dict, list)) else item}"
            for key, item in value.items()
        )
    return json.dumps(value)


def listener_registration_response(node_type: str | None, config: dict) -> dict:
    return {
        "status": "registered",
        "node_type": node_type,
        "config": {
            key: value
            for key, value in config.items()
            if key not in {"password", "client_secret"}
        },
        "message": "Inbound network nodes are registered as canvas node listeners; external listeners should post events to this node's incoming webhook endpoint.",
    }
