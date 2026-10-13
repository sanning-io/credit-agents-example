"""Each service's own `config.load` reads its own prefix and its own default
port and agent id."""

from __future__ import annotations

import pytest

from credit_demo.checker import config as checker_config
from credit_demo.underwriter import config as underwriter_config

# A real Ed25519 public key's shape: 32 bytes as 64 hex characters.
KEY = "ab" * 32


def test_underwriter_reads_underwriter_prefix():
    settings = underwriter_config.load({"UNDERWRITER_BUCKET": "uw-bucket", "CHECKER_BUCKET": "ck-bucket"})
    assert settings.service == "underwriter"
    assert settings.bucket == "uw-bucket"
    assert settings.port == 8001


def test_checker_reads_checker_prefix():
    settings = checker_config.load({"UNDERWRITER_BUCKET": "uw-bucket", "CHECKER_BUCKET": "ck-bucket"})
    assert settings.service == "checker"
    assert settings.bucket == "ck-bucket"
    assert settings.port == 8002


# ── The counterparty key is checked on its SHAPE, at start-up ───────────────


@pytest.mark.parametrize(
    "spelling",
    [
        "None",
        "none",
        "null",
        "unset",
        "TODO",
        "<the underwriter's key>",
        "0x" + KEY,  # the 0x a wallet tool prints
        '"' + KEY + '"',  # the quotes a Railway variable or `docker -e` carries
        KEY[:-1],  # one character short
        KEY + "ab",  # one byte long
        KEY[:-1] + "z",  # not hex
    ],
)
def test_a_value_that_is_not_a_key_refuses_to_load(spelling):
    """Present and not a key is not something to be lenient about.

    Every one of these is truthy, so read leniently the service would start
    healthy and fail each hand-off in turn, telling the operator the
    counterparty was not who it expected rather than that a named variable
    holds a typo. No hand-off is accepted on one of them either way: the
    comparison is whole-value and fails closed. What the check changes is
    the moment and the message.
    """
    for module, prefix in ((checker_config, "CHECKER"), (underwriter_config, "UNDERWRITER")):
        with pytest.raises(ValueError) as caught:
            module.load({f"{prefix}_COUNTERPARTY_PUBLIC_KEY": spelling})
        assert f"{prefix}_COUNTERPARTY_PUBLIC_KEY" in str(caught.value)
        # The value is never echoed: an operator who pasted a signing seed
        # into the wrong variable must not find it in a start-up log.
        assert KEY not in str(caught.value)
        assert repr(spelling) not in str(caught.value)


@pytest.mark.parametrize("spelling", [KEY, KEY.upper(), " " + KEY + " ", "  " + KEY.upper()])
def test_a_key_spelled_any_valid_way_still_loads(spelling):
    """The other half of a fail-closed check: it has to leave a real key
    alone. Hex is case-insensitive and the envelope layer pins the charset
    rather than the case, and an `.env` line carries whatever
    whitespace the operator typed."""
    settings = checker_config.load({"CHECKER_COUNTERPARTY_PUBLIC_KEY": spelling})
    assert settings.counterparty_public_key == KEY


def test_an_unset_counterparty_key_still_starts():
    """Unset stays unset and stays lenient: both services come up with no
    `.env` at all, and each says on `/health` what it is still missing."""
    settings = checker_config.load({})
    assert settings.counterparty_public_key == ""
    assert settings.as_health()["counterparty_key_configured"] is False
