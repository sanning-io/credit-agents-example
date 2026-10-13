"""The checker agent's two turns: the review of an assessment, and the
conclusion on the revision that answers its challenge (or on the assessment
alone, when the review agreed).

Both turns work one case, given as LangChain's runtime context and built
from the credit desk's answer at the start of the turn
(`credit_demo.checker.case_documents`): the tools find every case document
through it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..common.context import CaseContext
from ..common.turn import Turn
from .plan import CONCLUSION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT, conclusion_task, review_task
from .tools import CONCLUSION_FILENAME, REVIEW_FILENAME, build_conclusion_tools, build_review_tools


def review_turn(workspace: Path, case: CaseContext, *, assessment: Mapping[str, Any]) -> Turn:
    return Turn(
        name="review-assessment",
        tools=build_review_tools(workspace, assessment=assessment),
        system_prompt=REVIEW_SYSTEM_PROMPT,
        task=review_task(assessment, case),
        output=workspace / REVIEW_FILENAME,
        owed="a review",
        context=case,
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
        tools=build_conclusion_tools(workspace, revision=revision, challenge=challenge),
        system_prompt=CONCLUSION_SYSTEM_PROMPT,
        task=conclusion_task(assessment, review, challenge, revision, case),
        output=workspace / CONCLUSION_FILENAME,
        owed="a conclusion",
        context=case,
    )
