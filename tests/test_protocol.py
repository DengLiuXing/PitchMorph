"""协议解析的容错：父进程读的是管道，什么脏东西都可能出现，但绝不能让任务崩。"""

from __future__ import annotations

import json

from app.protocol import DONE, ERROR, PROGRESS, parse_line


def test_progress_line_parses():
    line = json.dumps({"t": PROGRESS, "frac": 0.42, "detail": "专家 2/4"})
    assert parse_line(line) == {"t": PROGRESS, "frac": 0.42, "detail": "专家 2/4"}


def test_done_line_parses():
    line = json.dumps({"t": DONE, "artifacts": {"vocals": "stems/vocals.wav"}})
    event = parse_line(line)
    assert event is not None
    assert event["artifacts"]["vocals"] == "stems/vocals.wav"


def test_error_line_parses():
    assert parse_line(json.dumps({"t": ERROR, "message": "boom"}))["message"] == "boom"


def test_trailing_newline_is_tolerated():
    assert parse_line('{"t": "progress", "frac": 0.1}\r\n') is not None


def test_half_line_returns_none():
    assert parse_line('{"t": "progress", "frac": 0.') is None


def test_empty_and_whitespace_return_none():
    assert parse_line("") is None
    assert parse_line("   \r\n") is None


def test_library_noise_returns_none():
    # torchfcpe / tqdm 之类往 stdout 漏一行时，不能当成事件
    assert parse_line("[INFO] device is not None, use cuda") is None
    assert parse_line(" 45%|####      | 5/11") is None


def test_unknown_type_returns_none():
    assert parse_line('{"t": "whatever"}') is None


def test_non_dict_json_returns_none():
    assert parse_line("[1, 2, 3]") is None
    assert parse_line('"just a string"') is None
