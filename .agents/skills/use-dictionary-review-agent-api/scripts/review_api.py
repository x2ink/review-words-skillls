#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用 easyjapanese 词条复审 Agent API，并记录本地审核日志。

next 子命令只读下一条待 AI 复审词条，不修改数据。submit 子命令从 UTF-8 JSON
文件或标准输入读取完整词条并提交；成功后会真实覆盖词条字段，同时写入审核记录的
ai_source 和 ai_reviewed_at。默认连接 EASYJAPANESE_AGENT_BASE_URL，未设置时使用
http://127.0.0.1:8000。每次成功提交都会在 review_audit_logs 中按词条 ID 保存
完整的 before 和 after；接口请求错误会自动追加到本地 JSONL 日志；log-issue
子命令为已原样提交的问题词条创建独立 JSON 文件，不调用接口。

用法：
    python review_api.py next
    python review_api.py submit --input payload.json --before-input .review_before.json
    python review_api.py log-audit --before-input before.json --after-input after.json
    python review_api.py log-issue --word-id 1 --word 建 --message "词形与读音冲突"
    python review_api.py log-uncertainty --word-id 1 --message "无法确认读音"
    Get-Content payload.json -Raw | python review_api.py submit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_LOG_PATH = ".agents/logs/agent_dictionary_review.jsonl"
DEFAULT_ISSUE_LOG_DIR = "review_issue_logs"
DEFAULT_AUDIT_LOG_DIR = "review_audit_logs"
DEFAULT_BEFORE_PATH = ".review_before.json"
NEXT_PATH = "/api/v2/agent/dictionary/reviews/next"
SUBMIT_PATH = "/api/v2/agent/dictionary/reviews/submit"


class InputError(ValueError):
    """表示提交数据在发出 HTTP 请求前未通过校验。"""


def write_json(value: Any, stream: BinaryIO, *, pretty: bool) -> None:
    indent = 2 if pretty else None
    separators = None if pretty else (",", ":")
    data = json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    ).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def decode_json(data: bytes) -> Any:
    if not data:
        raise ValueError("empty response body")
    return json.loads(data.decode("utf-8-sig"))


def unwrap_success_envelope(response: Any) -> Any:
    if not isinstance(response, dict):
        raise ValueError("response must be a JSON object")
    if response.get("code") != "OK":
        raise ValueError("successful response code must be OK")
    if "data" not in response:
        raise ValueError("successful response is missing data")
    return response["data"]


def concise_message(value: Any, *, max_length: int = 1000) -> str:
    if isinstance(value, dict):
        value = value.get("msg") or value.get("detail") or value
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def append_local_log(log_path: str, event: dict[str, Any]) -> str | None:
    path = Path(log_path)
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        **event,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    except OSError as exc:
        return str(exc)
    return None


def write_issue_file(
    issue_dir: str,
    *,
    word_id: int,
    words: list[str],
    uncertain_fields: list[str],
    message: str,
) -> tuple[str | None, str | None]:
    directory = Path(issue_dir)
    path = directory / f"word-{word_id}.json"
    temp_path = directory / f".word-{word_id}.json.tmp"
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event_type": "dictionary_review_issue",
        "submission_status": "submitted_original",
        "word_id": word_id,
        "words": list(dict.fromkeys(words)),
        "uncertain_fields": list(dict.fromkeys(uncertain_fields)),
        "reason": concise_message(message),
        "ai_source": os.getenv("EASYJAPANESE_AI_SOURCE", "chatgpt-5.6"),
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temp_path.replace(path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None, str(exc)
    return str(path), None


def write_object_file(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temp_path.replace(path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_audit_file(
    audit_dir: str,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[str | None, str | None]:
    word_id = after.get("id")
    directory = Path(audit_dir)
    path = directory / f"{word_id}.json"
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "word_id": word_id,
        "before": before,
        "after": after,
    }
    try:
        write_object_file(path, record)
    except OSError as exc:
        return None, str(exc)
    return str(path), None


def attach_log_error(output: dict[str, Any], log_error: str | None) -> None:
    if log_error:
        output["log_error"] = concise_message(log_error)


def request_json(
    base_url: str,
    path: str,
    *,
    method: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "easyjapanese-dictionary-review-skill/1.0",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=timeout) as response:
        return decode_json(response.read())


def read_payload(input_path: str) -> dict[str, Any]:
    try:
        if input_path == "-":
            raw = sys.stdin.buffer.read()
        else:
            raw = Path(input_path).read_bytes()
    except OSError as exc:
        raise InputError(f"could not read input JSON: {exc}") from exc

    try:
        payload = decode_json(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise InputError(f"invalid input JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InputError("submit payload must be a JSON object")
    return payload


def validate_submit_payload(payload: dict[str, Any]) -> None:
    allowed_top_level = {
        "id",
        "words",
        "kana",
        "tone",
        "detail",
        "rome",
        "description",
        "ai_source",
    }
    unexpected = set(payload).difference(allowed_top_level)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise InputError(f"unexpected top-level fields: {names}")

    word_id = payload.get("id")
    if not isinstance(word_id, int) or isinstance(word_id, bool) or word_id < 1:
        raise InputError("id must be a positive integer")

    words = payload.get("words")
    if not isinstance(words, list) or not words:
        raise InputError("words must be a non-empty array")
    if any(not isinstance(word, str) or not word.strip() for word in words):
        raise InputError("words must contain only non-empty strings")

    detail_items = payload.get("detail")
    if not isinstance(detail_items, list) or not detail_items:
        raise InputError("detail must be a non-empty array")

    for detail_index, detail in enumerate(detail_items):
        if not isinstance(detail, dict):
            raise InputError(f"detail[{detail_index}] must be an object")
        unexpected = set(detail).difference({"type", "meanings"})
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise InputError(f"unexpected detail fields: {names}")
        meanings = detail.get("meanings")
        if not isinstance(meanings, list) or not meanings:
            raise InputError(f"detail[{detail_index}].meanings must be non-empty")
        for meaning_index, meaning in enumerate(meanings):
            if not isinstance(meaning, dict):
                raise InputError(
                    f"detail[{detail_index}].meanings[{meaning_index}] must be an object"
                )
            if "jp" in meaning:
                raise InputError("meanings.jp is forbidden in agent review payloads")
            unexpected = set(meaning).difference({"zh", "examples"})
            if unexpected:
                names = ", ".join(sorted(unexpected))
                raise InputError(f"unexpected meaning fields: {names}")
            examples = meaning.get("examples", [])
            if not isinstance(examples, list):
                raise InputError("meanings.examples must be an array")
            for example in examples:
                if not isinstance(example, dict):
                    raise InputError("each example must be an object")
                forbidden = {"read", "voice"}.intersection(example)
                if forbidden:
                    names = ", ".join(sorted(forbidden))
                    raise InputError(f"forbidden example fields: {names}")
                unexpected = set(example).difference({"jp", "zh"})
                if unexpected:
                    names = ", ".join(sorted(unexpected))
                    raise InputError(f"unexpected example fields: {names}")


def validate_audit_pair(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    before_id = before.get("id")
    after_id = after.get("id")
    if not isinstance(before_id, int) or isinstance(before_id, bool) or before_id < 1:
        raise InputError("before.id must be a positive integer")
    if before_id != after_id:
        raise InputError("before.id must match submit payload id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch or submit easyjapanese AI dictionary review entries."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("EASYJAPANESE_AGENT_BASE_URL", DEFAULT_BASE_URL),
        help="FastAPI base URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print response JSON.",
    )
    parser.add_argument(
        "--log-path",
        default=os.getenv("EASYJAPANESE_REVIEW_LOG_PATH", DEFAULT_LOG_PATH),
        help="Local JSONL event log path.",
    )
    parser.add_argument(
        "--issue-dir",
        default=os.getenv("EASYJAPANESE_REVIEW_ISSUE_DIR", DEFAULT_ISSUE_LOG_DIR),
        help="Directory for one JSON file per problematic submitted entry.",
    )
    parser.add_argument(
        "--audit-dir",
        default=os.getenv("EASYJAPANESE_REVIEW_AUDIT_DIR", DEFAULT_AUDIT_LOG_DIR),
        help="Directory for one before/after JSON file per submitted entry.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    next_parser = subparsers.add_parser("next", help="Fetch one pending review entry.")
    next_parser.add_argument(
        "--save-to",
        default=DEFAULT_BEFORE_PATH,
        help="Save the exact unwrapped GET data for the later audit record.",
    )
    submit_parser = subparsers.add_parser("submit", help="Submit one reviewed entry.")
    submit_parser.add_argument(
        "--input",
        default="-",
        help="UTF-8 JSON file path, or - for standard input.",
    )
    submit_parser.add_argument(
        "--before-input",
        default=DEFAULT_BEFORE_PATH,
        help="UTF-8 JSON file containing the exact data returned by next.",
    )
    audit_parser = subparsers.add_parser(
        "log-audit",
        help="Write or retry one before/after audit file without calling the API.",
    )
    audit_parser.add_argument("--before-input", required=True)
    audit_parser.add_argument("--after-input", required=True)
    issue_parser = subparsers.add_parser(
        "log-issue",
        help="Write one per-entry issue file after an original submission.",
    )
    issue_parser.add_argument("--word-id", type=int, required=True)
    issue_parser.add_argument(
        "--word",
        action="append",
        dest="words",
        default=[],
        help="Problematic word form; repeat for multiple forms.",
    )
    issue_parser.add_argument(
        "--uncertain-field",
        action="append",
        dest="uncertain_fields",
        default=[],
        help="Problematic field name; repeat for multiple fields.",
    )
    issue_parser.add_argument("--message", required=True)
    uncertainty_parser = subparsers.add_parser(
        "log-uncertainty",
        help="Append one major-uncertainty event without calling the API.",
    )
    uncertainty_parser.add_argument("--word-id", type=int, required=True)
    uncertainty_parser.add_argument(
        "--uncertain-field",
        action="append",
        dest="uncertain_fields",
        default=[],
        help="Uncertain field name; repeat for multiple fields.",
    )
    uncertainty_parser.add_argument("--message", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "log-audit":
        try:
            before = read_payload(args.before_input)
            after = read_payload(args.after_input)
            validate_submit_payload(after)
            validate_audit_pair(before, after)
        except InputError as exc:
            write_json(
                {"error": "invalid_input", "message": str(exc)},
                sys.stderr.buffer,
                pretty=args.pretty,
            )
            return 2
        audit_path, audit_error = write_audit_file(
            args.audit_dir,
            before=before,
            after=after,
        )
        if audit_error:
            write_json(
                {
                    "error": "audit_log_error",
                    "message": concise_message(audit_error),
                    "audit_dir": args.audit_dir,
                    "word_id": after["id"],
                },
                sys.stderr.buffer,
                pretty=args.pretty,
            )
            return 6
        write_json(
            {"logged": True, "audit_path": audit_path, "word_id": after["id"]},
            sys.stdout.buffer,
            pretty=args.pretty,
        )
        return 0

    if args.command == "log-issue":
        if args.word_id < 1:
            write_json(
                {"error": "invalid_input", "message": "word-id must be positive"},
                sys.stderr.buffer,
                pretty=args.pretty,
            )
            return 2
        issue_path, issue_error = write_issue_file(
            args.issue_dir,
            word_id=args.word_id,
            words=args.words,
            uncertain_fields=args.uncertain_fields,
            message=args.message,
        )
        if issue_error:
            write_json(
                {
                    "error": "issue_log_error",
                    "message": concise_message(issue_error),
                    "issue_dir": args.issue_dir,
                    "word_id": args.word_id,
                },
                sys.stderr.buffer,
                pretty=args.pretty,
            )
            return 6
        write_json(
            {
                "logged": True,
                "issue_path": issue_path,
                "word_id": args.word_id,
            },
            sys.stdout.buffer,
            pretty=args.pretty,
        )
        return 0

    if args.command == "log-uncertainty":
        event = {
            "event_type": "major_uncertainty",
            "status": "SKIPPED_UNCERTAIN",
            "word_id": args.word_id,
            "uncertain_fields": list(dict.fromkeys(args.uncertain_fields)),
            "message": concise_message(args.message),
            "ai_source": os.getenv("EASYJAPANESE_AI_SOURCE", "chatgpt-5.6"),
        }
        log_error = append_local_log(args.log_path, event)
        if log_error:
            write_json(
                {
                    "error": "log_error",
                    "message": concise_message(log_error),
                    "log_path": args.log_path,
                },
                sys.stderr.buffer,
                pretty=args.pretty,
            )
            return 6
        write_json(
            {"logged": True, "log_path": args.log_path, "word_id": args.word_id},
            sys.stdout.buffer,
            pretty=args.pretty,
        )
        return 0

    payload: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    endpoint = NEXT_PATH if args.command == "next" else SUBMIT_PATH
    method = "GET" if args.command == "next" else "POST"

    try:
        if args.command == "next":
            result = unwrap_success_envelope(
                request_json(
                    args.base_url,
                    NEXT_PATH,
                    method="GET",
                    timeout=args.timeout,
                )
            )
            if result is not None and not isinstance(result, dict):
                raise ValueError("next response data must be an object or null")
            before_path = Path(args.save_to)
            if result is None:
                before_path.unlink(missing_ok=True)
            else:
                try:
                    write_object_file(before_path, result)
                except OSError as exc:
                    raise InputError(f"could not save before snapshot: {exc}") from exc
        else:
            payload = read_payload(args.input)
            before = read_payload(args.before_input)
            payload.setdefault(
                "ai_source",
                os.getenv("EASYJAPANESE_AI_SOURCE", "chatgpt-5.6"),
            )
            validate_submit_payload(payload)
            validate_audit_pair(before, payload)
            result = unwrap_success_envelope(
                request_json(
                    args.base_url,
                    SUBMIT_PATH,
                    method="POST",
                    timeout=args.timeout,
                    payload=payload,
                )
            )
            if not isinstance(result, dict):
                raise ValueError("submit response data must be an object")
    except InputError as exc:
        output = {"error": "invalid_input", "message": str(exc)}
        log_error = append_local_log(
            args.log_path,
            {
                "event_type": "api_request_error",
                "error_kind": "invalid_input",
                "action": args.command,
                "method": method,
                "endpoint": endpoint,
                "base_url": args.base_url,
                "word_id": payload.get("id") if payload else None,
                "message": concise_message(exc),
            },
        )
        attach_log_error(output, log_error)
        write_json(output, sys.stderr.buffer, pretty=args.pretty)
        return 2
    except HTTPError as exc:
        raw = exc.read()
        try:
            response: Any = decode_json(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            response = raw.decode("utf-8", errors="replace")
        output = {"error": "http_error", "status": exc.code, "response": response}
        log_error = append_local_log(
            args.log_path,
            {
                "event_type": "api_request_error",
                "error_kind": "http_error",
                "action": args.command,
                "method": method,
                "endpoint": endpoint,
                "base_url": args.base_url,
                "word_id": payload.get("id") if payload else None,
                "status_code": exc.code,
                "message": concise_message(response),
            },
        )
        attach_log_error(output, log_error)
        write_json(output, sys.stderr.buffer, pretty=args.pretty)
        return 3
    except (URLError, TimeoutError, OSError) as exc:
        output = {"error": "network_error", "message": str(exc)}
        log_error = append_local_log(
            args.log_path,
            {
                "event_type": "api_request_error",
                "error_kind": "network_error",
                "action": args.command,
                "method": method,
                "endpoint": endpoint,
                "base_url": args.base_url,
                "word_id": payload.get("id") if payload else None,
                "message": concise_message(exc),
            },
        )
        attach_log_error(output, log_error)
        write_json(output, sys.stderr.buffer, pretty=args.pretty)
        return 4
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        output = {"error": "invalid_response", "message": str(exc)}
        log_error = append_local_log(
            args.log_path,
            {
                "event_type": "api_request_error",
                "error_kind": "invalid_response",
                "action": args.command,
                "method": method,
                "endpoint": endpoint,
                "base_url": args.base_url,
                "word_id": payload.get("id") if payload else None,
                "message": concise_message(exc),
            },
        )
        attach_log_error(output, log_error)
        write_json(output, sys.stderr.buffer, pretty=args.pretty)
        return 5

    if args.command == "submit":
        assert payload is not None
        assert before is not None
        audit_path, audit_error = write_audit_file(
            args.audit_dir,
            before=before,
            after=payload,
        )
        if audit_error:
            log_error = append_local_log(
                args.log_path,
                {
                    "event_type": "audit_log_error",
                    "action": "submit",
                    "word_id": payload["id"],
                    "audit_dir": args.audit_dir,
                    "message": concise_message(audit_error),
                },
            )
            output = {
                "error": "audit_log_error",
                "submitted": True,
                "word_id": payload["id"],
                "audit_dir": args.audit_dir,
                "message": concise_message(audit_error),
                "retry": "log-audit",
            }
            attach_log_error(output, log_error)
            write_json(output, sys.stderr.buffer, pretty=args.pretty)
            return 6

    write_json(result, sys.stdout.buffer, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
