from __future__ import annotations

import csv
import io
import json
from typing import Any
import xml.etree.ElementTree as ET

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    SimpleHTMLExtractor,
    config_value,
    dict_to_xml,
    nested_value,
    set_nested_value,
    simple_yaml_dump,
    simple_yaml_parse,
    xml_to_dict,
)


async def execute_parser_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    message = dict(payload)
    prop = str(config_value(config, "property", "payload"))
    mode = str(config_value(config, "mode", "parse")).lower()
    value = nested_value(message, prop)
    try:
        if node_type == "json":
            result = (
                json.loads(value)
                if mode == "parse" and isinstance(value, str)
                else json.dumps(value)
            )
        elif node_type == "csv":
            delimiter = str(config_value(config, "delimiter", ","))[:1] or ","
            if mode == "parse":
                text = "" if value is None else str(value)
                reader = (
                    csv.DictReader(io.StringIO(text), delimiter=delimiter)
                    if config.get("has_header", True)
                    else csv.reader(io.StringIO(text), delimiter=delimiter)
                )
                result = list(reader)
            else:
                rows = value if isinstance(value, list) else [value]
                output = io.StringIO()
                if rows and all(isinstance(row, dict) for row in rows):
                    fieldnames = list({key for row in rows for key in row.keys()})
                    writer = csv.DictWriter(
                        output, fieldnames=fieldnames, delimiter=delimiter
                    )
                    if config.get("has_header", True):
                        writer.writeheader()
                    writer.writerows(rows)
                else:
                    writer = csv.writer(output, delimiter=delimiter)
                    for row in rows:
                        writer.writerow(row if isinstance(row, list) else [row])
                result = output.getvalue()
        elif node_type == "html":
            if mode == "parse":
                parser = SimpleHTMLExtractor()
                parser.feed("" if value is None else str(value))
                extract = str(config_value(config, "extract", "text"))
                result = (
                    parser.links
                    if extract == "links"
                    else value if extract == "raw" else " ".join(parser.text)
                )
            else:
                result = "" if value is None else str(value)
        elif node_type == "xml":
            if mode == "parse":
                root = ET.fromstring("" if value is None else str(value))
                result = xml_to_dict(root)
            else:
                if isinstance(value, dict) and len(value) == 1:
                    root_name, root_value = next(iter(value.items()))
                    element = dict_to_xml(root_name, root_value)
                else:
                    element = dict_to_xml("root", value)
                result = ET.tostring(element, encoding="unicode")
        elif node_type == "yaml":
            try:
                import yaml
            except Exception:
                yaml = None
            if mode == "parse":
                result = (
                    yaml.safe_load(value)
                    if yaml
                    else simple_yaml_parse("" if value is None else str(value))
                )
            else:
                result = (
                    yaml.safe_dump(value, sort_keys=False)
                    if yaml
                    else simple_yaml_dump(value)
                )
        else:
            result = value
    except Exception as exc:
        return NodeExecutionResult(False, None, None, str(exc))
    set_nested_value(message, prop, result)
    return NodeExecutionResult(
        True,
        None,
        {"status": f"{node_type}-{mode}", "message": message, "value": result},
    )
