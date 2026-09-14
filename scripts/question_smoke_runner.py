#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTION_FILE = REPO_ROOT / "config" / "question_smoke_questions.json"
DEFAULT_OUTPUT_FILE = REPO_ROOT / "question_smoke_results.json"
SDK_API_KEY_HEADER = "X-Monkeypatch-API-Key"


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    ok: bool
    status_code: int | None = None
    elapsed_ms: int | None = None
    answer: str = ""
    error: str | None = None
    matched_expectation: str | None = None
    response: dict[str, Any] | None = None


def _service_unavailable_result(
    *,
    question_id: str,
    category: str,
    question: str,
    elapsed_ms: int,
    target: str,
    exc: Exception,
) -> QuestionResult:
    return QuestionResult(
        id=question_id,
        category=category,
        question=question,
        ok=False,
        elapsed_ms=elapsed_ms,
        error=(
            f"service_unreachable: could not reach {target}. "
            "Start the backend/API gateway and verify the URL before running question smoke tests. "
            f"Original error: {type(exc).__name__}: {exc}"
        ),
    )


def _browser_failure_result(
    *,
    question_id: str,
    category: str,
    question: str,
    elapsed_ms: int,
    exc: Exception,
) -> QuestionResult:
    error_text = str(exc)
    lowered = error_text.lower()
    if "failed to fetch" in lowered or "net::err_connection" in lowered or "connection refused" in lowered:
        error = (
            "browser_fetch_failed: the UI could not reach one of its APIs. "
            "This should be fixed by pointing the UI at the running Kong/API URL, "
            "starting the required backend services, or resolving CORS/proxy routing. "
            f"Original error: {error_text}"
        )
    else:
        error = error_text
    return QuestionResult(
        id=question_id,
        category=category,
        question=question,
        ok=False,
        elapsed_ms=elapsed_ms,
        error=error,
    )


def _preflight_api(base_url: str, endpoint: str, timeout: float, headers: dict[str, str]) -> QuestionResult | None:
    url = f"{base_url.rstrip('/')}{endpoint}" if endpoint.startswith("/") else f"{base_url.rstrip('/')}/{endpoint}"
    started = time.perf_counter()
    payload = {
        "question": "Explain this entire assessment to me: Failed to fetch",
        "top_k": 1,
        "traversal_depth": 1,
        "path_limit_per_hit": 1,
        "use_retrieval": False,
        "user_id": "question-smoke-preflight",
        "role": "admin",
        "use_memory": False,
        "bypass_cache": True,
    }
    try:
        response = httpx.post(url, json=payload, timeout=min(timeout, 10.0), headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except httpx.RequestError as exc:
        return _service_unavailable_result(
            question_id="api_preflight",
            category="system_preflight",
            question=f"Check GraphRAG query endpoint at {url}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            target=url,
            exc=exc,
        )
    if response.status_code >= 400:
        return QuestionResult(
            id="api_preflight",
            category="system_preflight",
            question=f"Check GraphRAG query endpoint at {url}",
            ok=False,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            error=f"api_preflight_failed_status_{response.status_code}: {response.text[:500]}",
        )
    return None


def _load_questions(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError(f"{path} must contain a JSON object with a questions array.")
    return data


def _selected_questions(data: dict[str, Any], category: str | None, ids: set[str] | None) -> list[dict[str, Any]]:
    questions = []
    for item in data["questions"]:
        if category and item.get("category") != category:
            continue
        if ids and item.get("id") not in ids:
            continue
        questions.append(item)
    return questions


def _sentence_uncapitalize(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    return value[0].lower() + value[1:]


def _variant_question_text(question: str, variant_index: int, category: str) -> str:
    base = question.strip()
    templates = [
        "{base}",
        "Using current factory data, {lower_base}",
        "Answer from the local graph only: {base}",
        "For supervisor review, {lower_base}",
        "Give a concise operational answer: {base}",
        "Include the evidence source if available: {base}",
        "If this is integration-backed, call that out: {base}",
        "Audit this as a read-only question: {base}",
        "Check the latest available state and answer: {base}",
        "Explain any uncertainty in the answer: {base}",
    ]
    category_templates = {
        "world_model_reasoning": [
            "{base}",
            "Simulate before action: {base}",
            "Keep approval gates explicit: {base}",
            "What would the expected and risk states be if this scenario occurs? {base}",
            "As a reasoning-only layer, {lower_base}",
            "Before any downstream agent acts, {lower_base}",
        ],
        "module_control_boundary": [
            "{base}",
            "From the backend module-control boundary, {lower_base}",
            "Without relying on frontend hiding, {lower_base}",
            "Check this against integration-backed add-on rules: {base}",
        ],
        "compliance": [
            "{base}",
            "Apply RBAC and audit sensitivity: {base}",
            "For a regulated user review, {lower_base}",
            "Answer with any escalation requirement: {base}",
        ],
        "negative_controls": [
            "{base}",
            "Answer only if this is supported by local system data: {base}",
            "Do not browse; can the local graph answer this? {base}",
            "If unsupported, say so clearly: {base}",
        ],
    }
    pool = category_templates.get(category, templates)
    template = pool[variant_index % len(pool)]
    return template.format(base=base, lower_base=_sentence_uncapitalize(base))


def expand_questions_to_target(questions: list[dict[str, Any]], target_count: int | None) -> list[dict[str, Any]]:
    if not target_count or target_count <= len(questions):
        return questions
    expanded = [dict(item) for item in questions]
    seen_text = {str(item["question"]).strip().lower() for item in expanded}
    variant_round = 1
    while len(expanded) < target_count:
        added_this_round = 0
        for item in questions:
            if len(expanded) >= target_count:
                break
            category = str(item.get("category") or "uncategorized")
            text = _variant_question_text(str(item["question"]), variant_round, category)
            key = text.strip().lower()
            if key in seen_text:
                continue
            seen_text.add(key)
            variant = dict(item)
            variant["id"] = f"{item['id']}__v{variant_round}"
            variant["question"] = text
            variant["base_question_id"] = item["id"]
            variant["variant_round"] = variant_round
            expanded.append(variant)
            added_this_round += 1
        if added_this_round == 0:
            raise RuntimeError("Question expansion could not create enough unique variants.")
        variant_round += 1
    return expanded


def _contains_any(answer: str, needles: list[str]) -> tuple[bool, str | None]:
    normalized = answer.lower()
    for needle in needles:
        if str(needle).lower() in normalized:
            return True, str(needle)
    return not needles, None


def _request_payload(defaults: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "question": question["question"],
        "top_k": question.get("top_k", defaults.get("top_k", 8)),
        "traversal_depth": question.get("traversal_depth", defaults.get("traversal_depth", 1)),
        "path_limit_per_hit": question.get("path_limit_per_hit", defaults.get("path_limit_per_hit", 5)),
        "use_retrieval": question.get("use_retrieval", defaults.get("use_retrieval", False)),
        "user_id": question.get("user_id", defaults.get("user_id", "question-smoke-runner")),
        "role": question.get("role", defaults.get("role", "admin")),
        "session_id": question.get("session_id", f"question-smoke-{int(time.time())}"),
        "use_memory": question.get("use_memory", defaults.get("use_memory", False)),
        "bypass_cache": question.get("bypass_cache", defaults.get("bypass_cache", True)),
    }
    if question.get("collections") is not None:
        payload["collections"] = question["collections"]
    return payload


def run_api_questions(
    *,
    base_url: str,
    data: dict[str, Any],
    questions: list[dict[str, Any]],
    timeout: float,
    stop_on_failure: bool,
    headers: dict[str, str],
) -> list[QuestionResult]:
    endpoint = data.get("defaults", {}).get("endpoint", "/api/v1/agentos/query")
    url = f"{base_url.rstrip('/')}{endpoint}"
    defaults = data.get("defaults", {})
    results: list[QuestionResult] = []
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for index, question in enumerate(questions, start=1):
            started = time.perf_counter()
            try:
                response = client.post(url, json=_request_payload(defaults, question))
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                payload = response.json() if response.content else {}
                answer = str(payload.get("answer") or "")
                expected = question.get("expect_any") or []
                matched, matched_expectation = _contains_any(answer, expected)
                ok = response.status_code < 500 and bool(answer.strip()) and matched
                result = QuestionResult(
                    id=str(question.get("id") or index),
                    category=str(question.get("category") or "uncategorized"),
                    question=str(question["question"]),
                    ok=ok,
                    status_code=response.status_code,
                    elapsed_ms=elapsed_ms,
                    answer=answer,
                    matched_expectation=matched_expectation,
                    response=payload,
                    error=(None if ok else _failure_reason(response.status_code, answer, expected)),
                )
            except Exception as exc:
                if isinstance(exc, httpx.RequestError):
                    result = _service_unavailable_result(
                        question_id=str(question.get("id") or index),
                        category=str(question.get("category") or "uncategorized"),
                        question=str(question["question"]),
                        elapsed_ms=int((time.perf_counter() - started) * 1000),
                        target=url,
                        exc=exc,
                    )
                else:
                    result = QuestionResult(
                        id=str(question.get("id") or index),
                        category=str(question.get("category") or "uncategorized"),
                        question=str(question["question"]),
                        ok=False,
                        elapsed_ms=int((time.perf_counter() - started) * 1000),
                        error=str(exc),
                    )
            results.append(result)
            print_result(result, index=index, total=len(questions))
            if stop_on_failure and not result.ok:
                break
    return results


def _failure_reason(status_code: int, answer: str, expected: list[str]) -> str:
    if status_code == 502:
        return "bad_gateway_upstream_unavailable"
    if status_code >= 500:
        return f"server_error_status_{status_code}"
    if not answer.strip():
        return "empty_answer"
    if expected:
        return "answer_missing_expected_terms: " + ", ".join(str(item) for item in expected)
    return "unknown_failure"


def run_browser_questions(
    *,
    ui_url: str,
    data: dict[str, Any],
    questions: list[dict[str, Any]],
    timeout: float,
    input_selector: str,
    submit_selector: str,
    answer_selector: str,
    headless: bool,
    stop_on_failure: bool,
) -> list[QuestionResult]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Browser mode requires Playwright. Install it with: python3 -m pip install playwright && python3 -m playwright install chromium"
        ) from exc

    results: list[QuestionResult] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(ui_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        except Exception as exc:
            browser.close()
            return [
                QuestionResult(
                    id="browser_navigation",
                    category="browser_setup",
                    question=f"Open UI at {ui_url}",
                    ok=False,
                    elapsed_ms=0,
                    error=f"could_not_open_ui: {exc}",
                )
            ]
        for index, question in enumerate(questions, start=1):
            started = time.perf_counter()
            try:
                page.fill(input_selector, str(question["question"]))
                page.click(submit_selector)
                page.wait_for_selector(answer_selector, timeout=int(timeout * 1000))
                answer = page.locator(answer_selector).last.inner_text(timeout=int(timeout * 1000))
                expected = question.get("expect_any") or []
                matched, matched_expectation = _contains_any(answer, expected)
                result = QuestionResult(
                    id=str(question.get("id") or index),
                    category=str(question.get("category") or "uncategorized"),
                    question=str(question["question"]),
                    ok=bool(answer.strip()) and matched,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    answer=answer,
                    matched_expectation=matched_expectation,
                    error=None if matched else _failure_reason(200, answer, expected),
                )
            except Exception as exc:
                result = _browser_failure_result(
                    question_id=str(question.get("id") or index),
                    category=str(question.get("category") or "uncategorized"),
                    question=str(question["question"]),
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    exc=exc,
                )
            results.append(result)
            print_result(result, index=index, total=len(questions))
            if stop_on_failure and not result.ok:
                break
        browser.close()
    return results


def print_result(result: QuestionResult, *, index: int, total: int) -> None:
    marker = "PASS" if result.ok else "FAIL"
    elapsed = f"{result.elapsed_ms}ms" if result.elapsed_ms is not None else "-"
    print(f"[{index:03d}/{total:03d}] {marker} {result.id} ({result.category}) {elapsed}")
    if not result.ok:
        print(f"  question: {result.question}")
        print(f"  error: {result.error}")
        if result.answer:
            print(f"  answer: {result.answer[:500].replace(chr(10), ' ')}")


def write_results(path: Path, results: list[QuestionResult], started_at: float) -> None:
    passed = sum(1 for result in results if result.ok)
    payload = {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        },
        "results": [
            {
                "id": result.id,
                "category": result.category,
                "question": result.question,
                "ok": result.ok,
                "status_code": result.status_code,
                "elapsed_ms": result.elapsed_ms,
                "matched_expectation": result.matched_expectation,
                "answer": result.answer,
                "error": result.error,
                "response": result.response,
            }
            for result in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def parse_headers(raw_headers: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw_headers:
        if ":" not in item:
            raise ValueError(f"Header must be 'Name: value', got {item!r}")
        name, value = item.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def apply_sdk_api_key_header(headers: dict[str, str]) -> dict[str, str]:
    normalized_names = {name.lower() for name in headers}
    if SDK_API_KEY_HEADER.lower() in normalized_names or "authorization" in normalized_names:
        return headers
    api_key = os.getenv("MONKEYPATCH_API_KEY") or os.getenv("MONKEYPATCH_TOKEN")
    if api_key:
        headers[SDK_API_KEY_HEADER] = api_key.strip()
    return headers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sentinel-X question smoke tests.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTION_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--category", help="Run only one question category.")
    parser.add_argument("--ids", help="Comma-separated question ids to run.")
    parser.add_argument(
        "--target-count",
        type=int,
        default=None,
        help="Expand selected questions to this count. Defaults to config defaults.target_count unless --no-expand is set.",
    )
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="Run only the curated questions from the JSON file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N selected questions after expansion.",
    )
    parser.add_argument("--list", action="store_true", help="List questions and exit.")
    parser.add_argument("--mode", choices=("api", "browser"), default="api")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL, usually Kong.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--api-key", help=f"Monkeypatched SDK API key. Sent as {SDK_API_KEY_HEADER}.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help=f"Extra API header, e.g. '{SDK_API_KEY_HEADER}: mpk_live_...'.",
    )
    parser.add_argument(
        "--skip-api-preflight",
        action="store_true",
        help="Skip the API gateway health check before running.",
    )
    parser.add_argument("--ui-url", default="http://localhost:3000", help="UI URL for browser mode.")
    parser.add_argument(
        "--input-selector",
        default="textarea[name='question'], textarea, input[name='question']",
    )
    parser.add_argument(
        "--submit-selector",
        default="button[type='submit'], button:has-text('Ask'), button:has-text('Send')",
    )
    parser.add_argument(
        "--answer-selector",
        default="[data-testid='answer'], .answer, .chat-message, [role='article']",
    )
    parser.add_argument("--headed", action="store_true", help="Show browser in browser mode.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    data = _load_questions(args.questions)
    ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
    questions = _selected_questions(data, args.category, ids)
    target_count = None if args.no_expand or ids else args.target_count
    if target_count is None and not args.no_expand and not ids:
        target_count = int((data.get("defaults") or {}).get("target_count") or 0) or None
    questions = expand_questions_to_target(questions, target_count)
    if args.limit is not None:
        if args.limit < 1:
            print("--limit must be greater than zero.", file=sys.stderr)
            return 2
        questions = questions[: args.limit]
    if args.list:
        for item in questions:
            print(f"{item['id']}\t{item['category']}\t{item['question']}")
        return 0
    if not questions:
        print("No questions selected.", file=sys.stderr)
        return 2

    started_at = time.perf_counter()
    headers = parse_headers(args.header)
    if args.api_key:
        headers[SDK_API_KEY_HEADER] = args.api_key.strip()
    headers = apply_sdk_api_key_header(headers)
    if not args.skip_api_preflight:
        preflight_failure = _preflight_api(
            args.base_url,
            str((data.get("defaults") or {}).get("endpoint") or "/api/v1/agentos/query"),
            args.timeout,
            headers,
        )
        if preflight_failure is not None:
            print_result(preflight_failure, index=1, total=1)
            write_results(args.output, [preflight_failure], started_at)
            print(f"\nWrote {args.output}")
            print("Summary: 0/1 passed")
            return 1
    if args.mode == "api":
        results = run_api_questions(
            base_url=args.base_url,
            data=data,
            questions=questions,
            timeout=args.timeout,
            stop_on_failure=args.stop_on_failure,
            headers=headers,
        )
    else:
        results = run_browser_questions(
            ui_url=args.ui_url,
            data=data,
            questions=questions,
            timeout=args.timeout,
            input_selector=args.input_selector,
            submit_selector=args.submit_selector,
            answer_selector=args.answer_selector,
            headless=not args.headed,
            stop_on_failure=args.stop_on_failure,
        )
    write_results(args.output, results, started_at)
    failed = [result for result in results if not result.ok]
    print(f"\nWrote {args.output}")
    print(f"Summary: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
