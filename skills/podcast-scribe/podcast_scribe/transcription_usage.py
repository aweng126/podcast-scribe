"""Local request accounting; provider usage is evidence, never a billing estimate."""
from __future__ import annotations

from copy import deepcopy
import math


def _response_totals() -> dict:
    return {"successful_responses": 0, "missing_usage_responses": 0, "reported_usage": {}}


def _empty_report(model: str | None = None) -> dict:
    return {
        "schema_version": 1, "model": model,
        "current_run": {"requested_chunks": 0, "requested_audio_seconds": 0,
                        "submitted_audio_seconds": 0, "submitted_reference_seconds": 0,
                        "request_attempts": 0, "retry_attempts": 0, "failed_attempts": 0,
                        **_response_totals()},
        "reused_cache": {"chunks": 0, "audio_seconds": 0, **_response_totals()},
        "billing_total": None, "agent_tokens": None,
        "notes": ["current_run 仅记录本次新增请求；reused_cache 是历史成功响应，本次未重新请求。",
                  "submitted 时长按请求调用累计，包含重试；不代表实际传输完成或计费时长。",
                  "缺失的响应 usage 和失败尝试的实际用量未知；失败请求也可能已计费。",
                  "未估算账单；Agent 整理 token 由宿主平台统计，此处未知。"],
    }


def _number(value) -> bool:
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def _safe_usage(payload: dict | None) -> dict:
    """Keep only documented numeric usage, never arbitrary provider fields."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    kind = usage.get("type")
    if kind == "duration" and _number(usage.get("seconds")):
        return {"duration": {"seconds": usage["seconds"]}}
    fields = ("input_tokens", "output_tokens", "total_tokens")
    if kind != "tokens" or not all(_number(usage.get(key)) for key in fields):
        return {}
    result = {key: usage[key] for key in fields}
    details = usage.get("input_token_details")
    if isinstance(details, dict):
        values = {key: details[key] for key in ("audio_tokens", "text_tokens")
                  if _number(details.get(key))}
        if values:
            result["input_token_details"] = values
    return {"tokens": result}


def _add_numbers(target: dict, source: dict):
    for key, value in source.items():
        if isinstance(value, dict):
            _add_numbers(target.setdefault(key, {}), value)
        elif value is None or key in target and target[key] is None:
            target[key] = None
        elif _number(value):
            total = target.get(key, 0) + value
            target[key] = total if _number(total) else None


def _response(target: dict, payload: dict | None):
    target["successful_responses"] += 1
    usage = _safe_usage(payload)
    if usage:
        _add_numbers(target["reported_usage"], usage)
    else:
        target["missing_usage_responses"] += 1


class UsageReport:
    def __init__(self, model: str):
        self.data = _empty_report(model)
        self._requested = set()

    def attempt(self, chunk_id: str, duration: float, references: list[dict]):
        current = self.data["current_run"]
        if chunk_id in self._requested:
            current["retry_attempts"] += 1
        else:
            self._requested.add(chunk_id)
            current["requested_chunks"] += 1
            current["requested_audio_seconds"] += duration
        current["request_attempts"] += 1
        current["submitted_audio_seconds"] += duration
        current["submitted_reference_seconds"] += sum(ref["end"] - ref["start"] for ref in references)

    def failed(self):
        self.data["current_run"]["failed_attempts"] += 1

    def success(self, payload: dict):
        _response(self.data["current_run"], payload)

    def reuse(self, payload: dict | None, duration: float):
        cached = self.data["reused_cache"]
        cached["chunks"] += 1
        _add_numbers(cached, {"audio_seconds": duration if _number(duration) else None})
        _response(cached, payload)

    def snapshot(self) -> dict:
        return deepcopy(self.data)


def merge_reports(reports: list[dict]) -> dict:
    """Sum independent audio-range reports; an empty list made no API requests."""
    result = _empty_report()
    models = {report["model"] for report in reports if report.get("model")}
    result["model"] = next(iter(models)) if len(models) == 1 else None
    for report in reports:
        for section in ("current_run", "reused_cache"):
            _add_numbers(result[section], report[section])
    return result
