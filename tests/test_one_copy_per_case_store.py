"""The underwriter runs as one copy per case store: a second copy on the same
folder would pick up the first copy's half-built audit trails."""

from __future__ import annotations

import pytest

from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app
from tests.rig import CHECKER_SEED, UNDERWRITER_SEED, Plane, services_for
from tests.test_bar_a_case_end_to_end import _env


def _settings(root):
    return underwriter_settings(
        service="underwriter",
        prefix="UNDERWRITER",
        default_agent_id="underwriter",
        default_port=8001,
        env=_env("UNDERWRITER", UNDERWRITER_SEED, CHECKER_SEED, root / "underwriter-cases"),
    )


def test_a_second_copy_on_the_same_folder_refuses_to_start_and_says_why(repo_tmp_path):
    first = create_app(_settings(repo_tmp_path), services=services_for("underwriter", Plane(), repo_tmp_path))

    with pytest.raises(ValueError, match=r"another copy .* half-built audit trails.*UNDERWRITER_RUNS_DIR"):
        create_app(_settings(repo_tmp_path), services=services_for("underwriter", Plane(), repo_tmp_path))

    assert first.state.cases is not None


def test_a_copy_on_another_folder_starts(repo_tmp_path):
    first = create_app(_settings(repo_tmp_path / "one"), services=services_for("underwriter", Plane(), repo_tmp_path))
    other = create_app(_settings(repo_tmp_path / "two"), services=services_for("underwriter", Plane(), repo_tmp_path))

    assert first is not other
