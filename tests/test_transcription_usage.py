"""Usage is a bounded accounting summary, not a transcript or secret dump."""
import pytest

from podcast_scribe.transcription_usage import UsageReport, merge_reports


@pytest.mark.parametrize("usage", [None, {}, {"type": "unsupported", "secret": "credential"},
                                   {"type": "duration", "seconds": True},
                                   {"type": "duration", "seconds": float("nan")},
                                   {"type": "duration", "seconds": 10 ** 1000},
                                   {"type": "tokens", "input_tokens": -1, "output_tokens": 1, "total_tokens": 0}])
def test_missing_or_invalid_usage_is_unknown_not_zero(usage):
    report = UsageReport("test-model")
    report.success({"usage": usage})
    current = report.snapshot()["current_run"]
    assert current["missing_usage_responses"] == 1
    assert current["reported_usage"] == {}


def test_usage_overflow_remains_unknown_and_json_serializable():
    import json

    report = UsageReport("test-model")
    for _ in range(2):
        report.success({"usage": {"type": "duration", "seconds": 1e308}})
    snapshot = report.snapshot()
    assert snapshot["current_run"]["reported_usage"]["duration"]["seconds"] is None
    merged = merge_reports([snapshot, snapshot])
    assert merged["current_run"]["reported_usage"]["duration"]["seconds"] is None
    json.dumps(merged, allow_nan=False)


def test_merge_reports_keeps_new_and_cached_usage_and_units_separate():
    first, second = UsageReport("test-model"), UsageReport("test-model")
    first.attempt("chunk-0001", 60, [])
    first.success({"usage": {"type": "tokens", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                             "endpoint": "never-copy", "input_token_details": {"audio_tokens": 8, "secret": 9}}})
    second.attempt("chunk-0001", 20, [{"start": 3, "end": 6}])
    second.success({"usage": {"type": "duration", "seconds": 20}})
    second.reuse(None, 30)
    report = merge_reports([first.snapshot(), second.snapshot()])
    current = report["current_run"]
    assert report["model"] == "test-model"
    assert current["requested_chunks"] == 2 and current["requested_audio_seconds"] == 80
    assert current["submitted_reference_seconds"] == 3
    assert current["reported_usage"] == {"tokens": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                                    "input_token_details": {"audio_tokens": 8}},
                                          "duration": {"seconds": 20}}
    assert current["missing_usage_responses"] == 0
    assert report["reused_cache"]["chunks"] == 1
    assert report["reused_cache"]["missing_usage_responses"] == 1
    assert first.snapshot()["current_run"]["requested_audio_seconds"] == 60
    assert "never-copy" not in str(report) and "secret" not in str(report)
    empty = merge_reports([])
    assert empty["current_run"]["request_attempts"] == 0
    assert empty["current_run"]["missing_usage_responses"] == 0
