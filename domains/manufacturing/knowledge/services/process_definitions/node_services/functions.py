from __future__ import annotations

import asyncio
from typing import Any

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    coerce_config_value,
    config_value,
    delete_nested_value,
    nested_value,
    payload_message,
    rule_matches,
    set_nested_value,
)


def _node_red_value_type(value: Any) -> str:
    aliases = {
        "str": "string",
        "num": "number",
        "bool": "boolean",
    }
    normalized = str(value or "json").strip().lower()
    return aliases.get(normalized, normalized)


async def execute_function_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    message = dict(payload)
    if node_type == "change":
        for rule in config.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            operation = str(
                rule.get("operation") or rule.get("op") or rule.get("t") or "set"
            ).lower()
            prop = str(rule.get("property") or rule.get("p") or "")
            value = rule.get("value") if "value" in rule else rule.get("to")
            value_type = rule.get("value_type") or rule.get("tot")
            if operation == "set":
                set_nested_value(message, prop, coerce_config_value(value, value_type))
            elif operation in {"delete", "del"}:
                delete_nested_value(message, prop)
            elif operation in {"move", "mov"}:
                target = str(rule.get("target") or rule.get("to") or "")
                moved = nested_value(message, prop)
                delete_nested_value(message, prop)
                set_nested_value(message, target, moved)
        return NodeExecutionResult(
            True, None, {"status": "changed", "message": message}
        )
    if node_type == "switch":
        prop = str(config_value(config, "property", "payload"))
        actual = nested_value(message, prop)
        matched = []
        for index, rule in enumerate(config.get("rules") or []):
            if isinstance(rule, dict) and rule_matches(actual, rule):
                matched.append(index)
                if not bool(config.get("check_all", True)):
                    break
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "switched",
                "property": prop,
                "value": actual,
                "matched_outputs": matched,
            },
        )
    if node_type == "range":
        prop = str(config_value(config, "property", "payload"))
        out_prop = str(config_value(config, "output_property", prop))
        try:
            value = float(nested_value(message, prop))
            in_min = float(config_value(config, "input_min", 0))
            in_max = float(config_value(config, "input_max", 100))
            out_min = float(config_value(config, "output_min", 0))
            out_max = float(config_value(config, "output_max", 1))
        except (TypeError, ValueError):
            return NodeExecutionResult(
                False, None, None, "Range node requires numeric input and bounds"
            )
        scaled = (
            out_min
            if in_max == in_min
            else out_min + ((value - in_min) * (out_max - out_min) / (in_max - in_min))
        )
        if config.get("clamp", True):
            low, high = sorted((out_min, out_max))
            scaled = max(low, min(high, scaled))
        set_nested_value(message, out_prop, scaled)
        return NodeExecutionResult(
            True, None, {"status": "ranged", "message": message, "value": scaled}
        )
    if node_type == "template":
        template = str(config.get("template") or "")
        try:
            rendered = template.format(**message)
        except Exception:
            rendered = template
        set_nested_value(
            message, str(config_value(config, "output_property", "payload")), rendered
        )
        return NodeExecutionResult(
            True, None, {"status": "templated", "message": message, "value": rendered}
        )
    if node_type == "delay":
        delay_ms = max(0, min(int(config_value(config, "delay_ms", 1000)), 10000))
        await asyncio.sleep(delay_ms / 1000)
        return NodeExecutionResult(
            True, None, {"status": "delayed", "delay_ms": delay_ms, "message": message}
        )
    if node_type == "trigger":
        reset = config.get("reset")
        if reset not in (None, "") and str(message.get("payload")) == str(reset):
            return NodeExecutionResult(
                True, None, {"status": "reset", "message": message}
            )
        duration_ms = max(0, min(int(config_value(config, "duration_ms", 0)), 10000))
        op1_type = (
            config.get("op1type")
            or config.get("payload_type")
            or config.get("trigger_type")
            or "str"
        )
        op1 = (
            config.get("op1")
            if "op1" in config
            else config.get("payload", config.get("trigger"))
        )
        op2_type = (
            config.get("op2type")
            or config.get("second_payload_type")
            or config.get("then_payload_type")
            or "str"
        )
        op2 = (
            config.get("op2")
            if "op2" in config
            else config.get("second_payload", config.get("then_payload"))
        )
        first_message = dict(message)
        first_message["payload"] = coerce_config_value(
            op1, _node_red_value_type(op1_type)
        )
        then_message = dict(message)
        then_message["payload"] = coerce_config_value(
            op2, _node_red_value_type(op2_type)
        )
        downstream_message = then_message if op2 not in (None, "") else first_message
        if duration_ms:
            await asyncio.sleep(duration_ms / 1000)
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "triggered",
                "duration_ms": duration_ms,
                "message": downstream_message,
                "initial_message": first_message,
                "then_message": then_message,
                "messages": [
                    {
                        "payload": first_message.get("payload"),
                        "delay_ms": 0,
                        "payload_type": op1_type,
                    },
                    {
                        "payload": then_message.get("payload"),
                        "delay_ms": duration_ms,
                        "payload_type": op2_type,
                    },
                ],
                "payload_type": op1_type,
                "then_payload_type": op2_type,
                "extend_delay": bool(config.get("extend")),
            },
        )
    if node_type in {"filter", "rbe"}:
        prop = str(config_value(config, "property", "payload"))
        value = nested_value(message, prop)
        start = config.get("start")
        previous = config.get("previous", start)
        mode = str(config_value(config, "mode", "block-unless-value-changes")).lower()
        gap = float(config_value(config, "gap", 0) or 0)
        passed = True
        if mode == "block-unless-value-changes":
            passed = value != previous
        elif mode == "block-unless-value-changes-ignore-initial":
            passed = previous not in (None, "") and value != previous
        elif mode == "block-if-value-changes":
            passed = value == previous
        elif mode in {"narrowband", "deadband"}:
            try:
                delta = abs(float(value) - float(previous))
            except (TypeError, ValueError):
                delta = 0 if value == previous else gap + 1
            passed = delta <= gap if mode == "narrowband" else delta > gap
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "passed" if passed else "filtered",
                "passed": passed,
                "property": prop,
                "value": value,
                "previous": previous,
                "message": message if passed else None,
            },
        )
    if node_type == "function":
        code = str(config.get("code") or "return payload").strip()
        if code.startswith("return "):
            expression = code.removeprefix("return ").strip()
            value = (
                nested_value(message, expression)
                if expression not in {"payload", "message"}
                else message.get(expression) if expression == "payload" else message
            )
        else:
            value = message.get("payload")
        set_nested_value(
            message, str(config_value(config, "output_property", "payload")), value
        )
        return NodeExecutionResult(
            True, None, {"status": "functioned", "message": message, "value": value}
        )
    if node_type == "exec":
        command = str(config.get("command") or "").strip()
        if not command:
            return NodeExecutionResult(False, None, None, "Exec command is required")
        args = [str(arg) for arg in (config.get("args") or [])]
        if config.get("append_payload"):
            args.append(payload_message(payload, config))
        timeout_seconds = max(
            1, min(int(config_value(config, "timeout_seconds", 20)), 30)
        )
        cwd = str(config.get("cwd") or "").strip() or None
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except Exception as exc:
            return NodeExecutionResult(False, None, None, str(exc))
        body = {
            "status": "executed" if proc.returncode == 0 else "failed",
            "return_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
        return NodeExecutionResult(
            proc.returncode == 0,
            None,
            body,
            None if proc.returncode == 0 else body["stderr"],
        )
    return NodeExecutionResult(True, None, {"status": "queued", "message": message})
