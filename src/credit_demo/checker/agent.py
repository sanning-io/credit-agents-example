"""The checker agent's two turns: the review of an assessment, and the
conclusion on the revision that answers its challenge (or on the assessment
alone, when the review agreed).

Both turns work one case, given as LangChain's runtime context and built
from the credit desk's answer at the start of the turn
(`credit_demo.checker.case_documents`): the tools find every case document,
the folder to write to and the challenge being answered through it."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..common.context import CaseContext
from ..common.messages import turn_unfinished
from ..common.turn import Turn
from .plan import CONCLUSION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT, conclusion_task, review_task
from .tools import CONCLUSION_FILENAME, CONCLUSION_TOOLS, REVIEW_FILENAME, REVIEW_TOOLS


def review_turn(workspace: Path, case: CaseContext, *, assessment: Mapping[str, Any]) -> Turn:
    return Turn(
        name="review-assessment",
        tools=list(REVIEW_TOOLS),
        system_prompt=REVIEW_SYSTEM_PROMPT,
        task=review_task(assessment, case),
        output=workspace / REVIEW_FILENAME,
        owed="a review",
        unfinished=turn_unfinished("checker", "review"),
        context=replace(case, workspace=workspace),
    )


def conclusion_turn(
    workspace: Path,
    case: CaseContext,
    *,
    assessment: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    revision: Mapping[str, Any] | None,
    challenge: Mapping[str, Any] | None,
) -> Turn:
    return Turn(
        name="conclude-case",
        tools=list(CONCLUSION_TOOLS),
        system_prompt=CONCLUSION_SYSTEM_PROMPT,
        task=conclusion_task(assessment, review, challenge, revision, case),
        output=workspace / CONCLUSION_FILENAME,
        owed="a conclusion",
        unfinished=turn_unfinished("checker", "conclusion"),
        context=replace(case, workspace=workspace, challenge=challenge),
    )
