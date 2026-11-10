"""What the image holds, checked without docker.

The container installs this package into site-packages, not as a checkout,
and a package installed that way looks for the documents inside the Python
installation unless it is told where its home is. The image's own check in
CI only asks `/health`, which reads no document.

So this reproduces the image's layout here. It copies what the Dockerfile's
`COPY` instructions copy into a directory standing in for its `WORKDIR`,
installs the package from there the way its `RUN pip install .` does (not
editable), and runs Python against that install alone, with the Dockerfile's
own `ENV` values, from that directory. What is asserted is what an agent in
the container reads: the four case documents at the paths the installed
package resolves, the credit policy's text read out of them, and the terms the
application requests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import dockerfile

REPO = Path(__file__).resolve().parents[1]


def _image_layout(root: Path) -> Path:
    """The Dockerfile's WORKDIR, holding what its COPY instructions copy, by
    Docker's rules: a directory's contents are copied into the destination,
    and a file lands inside a destination that ends in `/` or takes several
    sources."""
    workdir = root / dockerfile.workdir().lstrip("/")
    workdir.mkdir(parents=True)
    for sources, destination in dockerfile.copies():
        target = workdir / destination
        into = destination.endswith("/") or len(sources) > 1
        for source in sources:
            origin = REPO / source
            if origin.is_dir():
                shutil.copytree(
                    origin, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                (target if into else target.parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target / origin.name if into else target)
    return workdir


def _installed(workdir: Path, site: Path) -> None:
    """`pip install .` from the image's WORKDIR into `site`: a wheel, not an
    editable install, and no dependencies, which the running interpreter
    already has."""
    finished = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target", str(site), "."],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert finished.returncode == 0, finished.stderr


def _in_the_image(workdir: Path, site: Path, code: str, env: dict[str, str]) -> dict:
    """Run `code` against the installed package only, from the WORKDIR, with
    the image's environment. The checkout's `src` is not on the path."""
    dependencies = [
        entry for entry in sys.path
        if entry and Path(entry).resolve() not in {REPO.resolve(), (REPO / "src").resolve()}
    ]
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("CREDIT_DEMO_") and key not in {"CHECKER_URL", "DESK_URL"}
    }
    # A path the image names under its WORKDIR names the stand-in for it here.
    image_root = dockerfile.workdir().rstrip("/")
    environment.update({
        key: str(workdir) + value[len(image_root):]
        if value == image_root or value.startswith(image_root + "/") else value
        for key, value in env.items()
    })
    environment["PYTHONPATH"] = os.pathsep.join([str(site), *dependencies])
    finished = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout.strip().splitlines()[-1])


_READ_THE_DOCUMENTS = """
import json
import credit_demo
from credit_demo.common import application, documents, home
from credit_demo.underwriter.config import load
out = {
    "package": credit_demo.__file__,
    "home": str(documents.REPO_ROOT),
    "missing": home.missing_documents(documents.REPO_ROOT),
    "run_missing": list(load().ready_to_record()),
    "window": None,
    "amount": None,
}
if not out["missing"]:
    out["window"] = "24 months" in documents.read_pdf_text(documents.REPO_ROOT / home.CREDIT_POLICY)
    out["amount"] = application.terms(
        application.read(documents.REPO_ROOT / home.SAMPLE_APPLICATION)
    ).amount_sek
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    root = tmp_path_factory.mktemp("image")
    workdir = _image_layout(root)
    site = root / "site-packages"
    _installed(workdir, site)
    return workdir, site


def test_the_installed_package_in_the_image_reads_the_documents_it_ships(image):
    workdir, site = image

    seen = _in_the_image(workdir, site, _READ_THE_DOCUMENTS, dockerfile.env())

    assert Path(seen["package"]).resolve().is_relative_to(site.resolve()), (
        f"the check ran against {seen['package']}, not the installed package"
    )
    assert seen["missing"] == [], (
        f"the installed package looks for the documents under {seen['home']} and "
        f"does not find {seen['missing']}"
    )
    assert seen["window"] is True, seen
    assert seen["amount"] == 4_200_000, seen


def test_an_installed_package_with_no_home_says_so_before_a_run_starts(image):
    """Fails closed. Without the documents, the setting that names where they
    are is one a run is missing, named like the others, and not a path an
    agent opens halfway through a turn."""
    workdir, site = image
    env = {key: value for key, value in dockerfile.env().items() if key != "CREDIT_DEMO_HOME"}

    seen = _in_the_image(workdir, site, _READ_THE_DOCUMENTS, env)

    assert seen["missing"], seen
    assert "CREDIT_DEMO_HOME" in seen["run_missing"], seen


def test_the_image_binds_every_interface_over_ipv4_and_ipv6_and_says_so_explicitly():
    """Outside a container both services listen on loopback. Inside one, the
    underwriter's port has to be reachable from the host's network and the
    underwriter has to reach the checker's, and that is an instruction in the
    Dockerfile rather than a default anybody inherits."""
    assert dockerfile.env().get("CREDIT_DEMO_HOST") == "::"


def test_the_image_starts_the_underwriter_and_lists_no_port_for_the_checker():
    """One image, one service per container. The default command is the
    underwriter, the one service that has a public address, and the checker
    (started with `credit-demo checker`) has no endpoint that asks for a
    login, so its port is not one the image lists."""
    instructions = dockerfile.instructions()
    assert ("CMD", '["credit-demo", "underwriter"]') in instructions
    assert [args for keyword, args in instructions if keyword == "EXPOSE"] == ["8001"]
