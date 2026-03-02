"""Tests for remo.core.output module."""

from __future__ import annotations

import builtins

import pytest

from remo_cli.core.output import (
    BLUE,
    GREEN,
    NC,
    RED,
    YELLOW,
    confirm,
    print_error,
    print_info,
    print_success,
    print_warning,
)


class TestPrintError:
    """Tests for print_error()."""

    def test_writes_to_stderr(self, capsys):
        print_error("something went wrong")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "something went wrong" in captured.err

    def test_includes_red_ansi(self, capsys):
        print_error("fail")
        captured = capsys.readouterr()
        assert RED in captured.err

    def test_includes_error_prefix(self, capsys):
        print_error("fail")
        captured = capsys.readouterr()
        assert "Error:" in captured.err

    def test_includes_reset_code(self, capsys):
        print_error("fail")
        captured = capsys.readouterr()
        assert NC in captured.err

    def test_exact_format(self, capsys):
        print_error("disk full")
        captured = capsys.readouterr()
        assert captured.err == f"{RED}Error:{NC} disk full\n"

    def test_empty_message(self, capsys):
        print_error("")
        captured = capsys.readouterr()
        assert captured.err == f"{RED}Error:{NC} \n"


class TestPrintSuccess:
    """Tests for print_success()."""

    def test_writes_to_stdout(self, capsys):
        print_success("done")
        captured = capsys.readouterr()
        assert "done" in captured.out
        assert captured.err == ""

    def test_includes_green_ansi(self, capsys):
        print_success("done")
        captured = capsys.readouterr()
        assert GREEN in captured.out

    def test_includes_reset_code(self, capsys):
        print_success("done")
        captured = capsys.readouterr()
        assert NC in captured.out

    def test_exact_format(self, capsys):
        print_success("all good")
        captured = capsys.readouterr()
        assert captured.out == f"{GREEN}all good{NC}\n"


class TestPrintInfo:
    """Tests for print_info()."""

    def test_writes_to_stdout(self, capsys):
        print_info("info message")
        captured = capsys.readouterr()
        assert "info message" in captured.out
        assert captured.err == ""

    def test_includes_blue_ansi(self, capsys):
        print_info("info")
        captured = capsys.readouterr()
        assert BLUE in captured.out

    def test_includes_reset_code(self, capsys):
        print_info("info")
        captured = capsys.readouterr()
        assert NC in captured.out

    def test_exact_format(self, capsys):
        print_info("loading")
        captured = capsys.readouterr()
        assert captured.out == f"{BLUE}loading{NC}\n"


class TestPrintWarning:
    """Tests for print_warning()."""

    def test_writes_to_stdout(self, capsys):
        print_warning("watch out")
        captured = capsys.readouterr()
        assert "watch out" in captured.out
        assert captured.err == ""

    def test_includes_yellow_ansi(self, capsys):
        print_warning("warning")
        captured = capsys.readouterr()
        assert YELLOW in captured.out

    def test_includes_reset_code(self, capsys):
        print_warning("warning")
        captured = capsys.readouterr()
        assert NC in captured.out

    def test_exact_format(self, capsys):
        print_warning("careful")
        captured = capsys.readouterr()
        assert captured.out == f"{YELLOW}careful{NC}\n"


class TestConfirm:
    """Tests for confirm()."""

    @pytest.mark.parametrize(
        "answer",
        ["yes", "y", "ye", "yeah", "yep", "yup", "sure", "ok"],
    )
    def test_affirmative_lowercase(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert confirm("Continue?") is True

    @pytest.mark.parametrize(
        "answer",
        ["YES", "Y", "YE", "YEAH", "YEP", "YUP", "SURE", "OK"],
    )
    def test_affirmative_uppercase(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert confirm("Continue?") is True

    @pytest.mark.parametrize(
        "answer",
        ["Yes", "Yeah", "Sure", "Ok", "Yep"],
    )
    def test_affirmative_mixed_case(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert confirm("Continue?") is True

    @pytest.mark.parametrize(
        "answer",
        ["no", "n", "nah", "nope", "never", "x", "abc", "0"],
    )
    def test_negative_responses(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert confirm("Continue?") is False

    def test_empty_input_default_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert confirm("Continue?", default=False) is False

    def test_empty_input_default_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert confirm("Continue?", default=True) is True

    def test_whitespace_only_input_default_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "   ")
        assert confirm("Continue?", default=False) is False

    def test_whitespace_only_input_default_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "   ")
        assert confirm("Continue?", default=True) is True

    def test_eoferror_returns_default_false(self, monkeypatch):
        def raise_eof(_):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert confirm("Continue?", default=False) is False

    def test_eoferror_returns_default_true(self, monkeypatch):
        def raise_eof(_):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert confirm("Continue?", default=True) is True

    def test_prompt_suffix_default_false(self, monkeypatch):
        prompts_seen = []

        def capture_input(prompt):
            prompts_seen.append(prompt)
            return "y"

        monkeypatch.setattr("builtins.input", capture_input)
        confirm("Continue?", default=False)
        assert "[y/N]" in prompts_seen[0]

    def test_prompt_suffix_default_true(self, monkeypatch):
        prompts_seen = []

        def capture_input(prompt):
            prompts_seen.append(prompt)
            return "y"

        monkeypatch.setattr("builtins.input", capture_input)
        confirm("Continue?", default=True)
        assert "[Y/n]" in prompts_seen[0]

    def test_leading_trailing_whitespace_stripped(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "  yes  ")
        assert confirm("Continue?") is True
