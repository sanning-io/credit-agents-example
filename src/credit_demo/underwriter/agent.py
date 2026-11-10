"""The underwriting agent's two turns: the assessment, and the revision it writes when the checker
agent challenges it. Both turns use the same tools and work one case through LangChain's runtime
context."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..common.context import CaseContext
from ..common.messages import turn_unfinished
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
        unfinished=turn_unfinished("underwriter", "assessment"),
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
        unfinished=turn_unfinished("underwriter", "revision"),
        context=replace(case, workspace=workspace, challenge=challenge),
    )
