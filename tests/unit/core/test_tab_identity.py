"""Tab identity detection and key derivation (spec 028 R3/R4)."""

from __future__ import annotations

import pytest

from remo_cli.core.tab_identity import (
    TAB_KEY_RE,
    TAB_VARIABLES,
    TabIdentity,
    derive_tab_key,
    detect_tab_identity,
)


def test_precedence_order_is_the_documented_one():
    assert TAB_VARIABLES == (
        "TMUX_PANE",
        "WEZTERM_PANE",
        "KITTY_WINDOW_ID",
        "ITERM_SESSION_ID",
        "TERM_SESSION_ID",
        "WT_SESSION",
    )


@pytest.mark.parametrize("index", range(len(TAB_VARIABLES) - 1))
def test_earlier_variable_beats_later(index):
    winner = TAB_VARIABLES[index]
    env = {name: "v" for name in TAB_VARIABLES[index:]}
    found = detect_tab_identity(env)
    assert found is not None
    assert found.variable == winner


@pytest.mark.parametrize("variable", TAB_VARIABLES[1:])
def test_each_non_tmux_variable_alone(variable):
    found = detect_tab_identity({variable: "abc"})
    assert found == TabIdentity(variable, "abc")
    assert found.identity == f"{variable}=abc"


def test_empty_values_count_as_unset():
    assert detect_tab_identity({"TMUX_PANE": "", "WT_SESSION": ""}) is None
    found = detect_tab_identity({"TMUX_PANE": "", "KITTY_WINDOW_ID": "7"})
    assert found == TabIdentity("KITTY_WINDOW_ID", "7")


def test_none_set_is_none():
    assert detect_tab_identity({"PATH": "/bin"}) is None
    assert detect_tab_identity({}) is None


def test_tmux_uses_socket_field_and_ignores_pid():
    a = detect_tab_identity({"TMUX": "/tmp/tmux-1000/default,111,0", "TMUX_PANE": "%3"})
    b = detect_tab_identity({"TMUX": "/tmp/tmux-1000/default,999,4", "TMUX_PANE": "%3"})
    assert a == b
    assert a is not None
    assert a.value == "/tmp/tmux-1000/default:%3"


def test_tmux_pane_without_tmux_still_yields_identity():
    found = detect_tab_identity({"TMUX_PANE": "%1"})
    assert found == TabIdentity("TMUX_PANE", ":%1")


def test_same_value_under_different_variables_differs():
    a = detect_tab_identity({"KITTY_WINDOW_ID": "1"})
    b = detect_tab_identity({"WEZTERM_PANE": "1"})
    assert a is not None and b is not None
    assert a.identity != b.identity
    secret = b"s" * 32
    assert derive_tab_key(a, secret) != derive_tab_key(b, secret)


def test_derive_tab_key_shape_determinism_and_secret_dependence():
    ident = TabIdentity("KITTY_WINDOW_ID", "1")
    key = derive_tab_key(ident, b"a" * 32)
    assert TAB_KEY_RE.match(key)
    assert key == derive_tab_key(ident, b"a" * 32)
    assert key != derive_tab_key(ident, b"b" * 32)


def test_undecodable_environment_bytes_do_not_raise():
    """os.environ surfaces non-UTF-8 bytes as lone surrogates; deriving a key
    from one must not raise and block `remo shell` (R5)."""
    raw = b"/tmp/tmux-\xff/default".decode("utf-8", "surrogateescape")
    found = detect_tab_identity({"TMUX_PANE": "%1", "TMUX": f"{raw},1,0"})
    assert found is not None
    key = derive_tab_key(found, b"a" * 32)
    assert TAB_KEY_RE.match(key)
    assert key != derive_tab_key(TabIdentity("TMUX_PANE", "/tmp/tmux-/default:%1"), b"a" * 32)
