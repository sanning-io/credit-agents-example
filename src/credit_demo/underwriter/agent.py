"""The underwriting agent's two turns: the assessment, and the revision it
writes when the checker agent challenges it.

The revision is a second turn of the same agent, with the same tools. It is
given its own assessment and the challenge exactly as the checker agent
signed it.

Both turns work one case, given as LangChain's runtime context: the tools
find every case document, the folder to write to and the challenge being
answered through it."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..common.context import CaseContext
from ..common.turn import Turn
from .plan import REVISION_SYSTEM_PROMPT, SYSTEM_PROMPT, revision_task, task
from .tools import ASSESSMENT_FILENAME, ASSESSMENT_TOOLS, REVISION_FILENAME, REVISION_TOOLS


def assessment_turn(workspace: Path, case: CaseContext) -> Turn:
    return Turn(
        name="underwrite-case",
        tools=list(ASSESSMENT_TOOLS),
        system_prompt=SYSTEM_PROMPT,
        task=task(case),
        output=workspace / ASSESSMENT_FILENAME,
        owed="an assessment",
        context=replace(case, workspace=workspace),
    )


def revision_turn(
    workspace: Path,
    case: CaseContext,
    *,
    assessment: Mapping[str, Any],
    challenge: Mapping[str, Any],
) -> Turn:
    return Turn(
        name="revise-case",
        tools=list(REVISION_TOOLS),
        system_prompt=REVISION_SYSTEM_PROMPT,
        task=revision_task(assessment, challenge, case),
        output=workspace / REVISION_FILENAME,
        owed="a revision",
        context=replace(case, workspace=workspace, challenge=challenge),
    )
