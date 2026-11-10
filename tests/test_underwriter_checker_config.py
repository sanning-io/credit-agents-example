"""Each service's own `config.load` has its own default port and agent id, and
refuses a counterparty key that is not a key."""

from __future__ import annotations

import pytest

from credit_demo.checker import config as checker_config
from credit_demo.underwriter import config as underwriter_config

# A real Ed25519 public key's shape: 32 bytes as 64 hex characters.
KEY = "ab" * 32


def test_each_service_has_its_own_default_port_and_agent_id():
    underwriter = underwriter_config.load({})
    checker = checker_config.load({})
    assert (underwriter.service, underwriter.port, underwriter.agent_id) == ("underwriter", 8001, "underwriter")
    assert (checker.service, checker.port, checker.agent_id) == ("checker", 8002, "checker")


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
        '"' + KEY + '"',  # the quotes a hosting dashboard variable or `docker -e` carries
        KEY[:-1],  # one character short
        KEY + "ab",  # one byte long
        KEY[:-1] + "z",  # not hex
    ],
)
def test_a_value_that_is_not_a_key_refuses_to_load(spelling):
    """A value that is present and is not a key is truthy, so a lenient read would start the service
    healthy and fail each hand-off. Refusing at start-up names the variable. Hand-offs fail
    closed either way: the comparison is whole-value."""
    for module in (checker_config, underwriter_config):
        with pytest.raises(ValueError) as caught:
            module.load({"COUNTERPARTY_PUBLIC_KEY": spelling})
        assert "COUNTERPARTY_PUBLIC_KEY" in str(caught.value)
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
    settings = checker_config.load({"COUNTERPARTY_PUBLIC_KEY": spelling})
    assert settings.counterparty_public_key == KEY


def test_an_unset_counterparty_key_still_starts():
    """Unset stays unset and stays lenient: both services come up with no
    `.env` at all, and each says on `/health` what it is still missing."""
    settings = checker_config.load({})
    assert settings.counterparty_public_key == ""
    assert settings.as_health()["counterparty_key_configured"] is False
