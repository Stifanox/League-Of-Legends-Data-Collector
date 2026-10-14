"""Tests for API key masking in crawler.logging_setup."""

from __future__ import annotations

import logging

import pytest

from crawler.logging_setup import LOG_FILE_NAME, mask_secrets, setup_logging

KEY = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"


@pytest.fixture
def log_file(tmp_path):
    setup_logging(tmp_path)
    yield tmp_path / LOG_FILE_NAME
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_crawler_handler", False):
            root.removeHandler(handler)
            handler.close()


def read_log(path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_mask_secrets():
    assert mask_secrets(f"key={KEY}&x=1") == "key=RGAPI-****&x=1"
    assert mask_secrets("nothing secret") == "nothing secret"


def test_plain_message(log_file, capsys):
    logging.getLogger("test").warning("token RGAPI-abcdef01-2345 leaked")
    assert "RGAPI-abcdef01" not in read_log(log_file)
    assert "token RGAPI-**** leaked" in read_log(log_file)
    err = capsys.readouterr().err
    assert "token RGAPI-**** leaked" in err
    assert "RGAPI-abcdef01" not in err


def test_percent_args(log_file, capsys):
    logging.getLogger("test").info("using key %s for %s", KEY, "eun1")
    text = read_log(log_file)
    assert KEY not in text
    assert "using key RGAPI-**** for eun1" in text
    err = capsys.readouterr().err
    assert "using key RGAPI-**** for eun1" in err
    assert KEY not in err


def test_dict_args(log_file):
    logging.getLogger("test").info("headers %(token)s", {"token": KEY})
    assert "headers RGAPI-****" in read_log(log_file)


def test_non_string_arg_containing_key(log_file):
    logging.getLogger("test").info("headers %s", {"X-Riot-Token": KEY})
    text = read_log(log_file)
    assert KEY not in text
    assert "RGAPI-****" in text


def test_numeric_args_untouched(log_file):
    logging.getLogger("test").info("%d requests, %.1f%%", 7, 12.5)
    assert "7 requests, 12.5%" in read_log(log_file)


def test_f_string(log_file):
    logging.getLogger("test").error(f"GET https://x?api_key={KEY} failed")
    text = read_log(log_file)
    assert KEY not in text
    assert "api_key=RGAPI-**** failed" in text


def test_logged_exception(log_file, capsys):
    try:
        raise RuntimeError(f"bad request with {KEY}")
    except RuntimeError:
        logging.getLogger("test").exception("request failed")
    text = read_log(log_file)
    assert KEY not in text
    assert "RuntimeError: bad request with RGAPI-****" in text
    err = capsys.readouterr().err
    assert "bad request with RGAPI-****" in err
    assert KEY not in err


def test_stack_info(log_file):
    logging.getLogger("test").info(f"with stack {KEY}", stack_info=True)
    assert KEY not in read_log(log_file)


def test_setup_is_idempotent(tmp_path, log_file):
    setup_logging(tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_crawler_handler", False)]
    assert len(ours) == 2


def test_console_hides_debug(log_file, capsys):
    logging.getLogger("test").debug("debug detail")
    assert "debug detail" in read_log(log_file)
    assert "debug detail" not in capsys.readouterr().err
