from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .assistant import AssistantDraft, AssistantService

MAX_DRAFT_SESSION_USER_TURNS = 20
MAX_DRAFT_SESSIONS = 128
DRAFT_SESSION_TTL = timedelta(hours=1)


class DraftSessionError(RuntimeError):
    """Base class for draft-session failures."""


class DraftSessionNotFoundError(DraftSessionError):
    """Raised when a draft session does not exist or has expired."""


class DraftSessionConflictError(DraftSessionError):
    """Raised when a stale or concurrent session mutation is attempted."""


class DraftSessionCapacityError(DraftSessionError):
    """Raised when every retained session is actively compiling."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftSessionTurn(_StrictModel):
    turn_id: str
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000)
    created_at: datetime
    draft_id: str | None = None


class DraftSessionExecution(_StrictModel):
    workflow_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    draft_id: str = Field(min_length=1, max_length=64)
    confirmed_revision: int = Field(ge=1)
    submitted_at: datetime


class DraftSession(_StrictModel):
    session_id: str
    locale: Literal["zh-CN", "en"]
    revision: int = Field(ge=1)
    status: Literal["proposal", "needs_clarification", "unsupported"]
    busy: bool
    turns: list[DraftSessionTurn] = Field(min_length=2)
    draft: AssistantDraft
    execution: DraftSessionExecution | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_session(self) -> DraftSession:
        user_turns = sum(turn.role == "user" for turn in self.turns)
        if user_turns != self.revision:
            raise ValueError("session revision must equal its user-turn count")
        if self.turns[-1].role != "assistant":
            raise ValueError("completed sessions must end with an assistant turn")
        if self.status != self.draft.status:
            raise ValueError("session status must match its current draft")
        if self.execution is not None:
            if self.status != "proposal":
                raise ValueError("only a proposal session can have an execution")
            if self.execution.confirmed_revision != self.revision:
                raise ValueError("execution revision must match the session revision")
            if self.execution.draft_id != self.draft.provenance.draft_id:
                raise ValueError("execution draft must match the current draft")
        return self


@dataclass
class _SessionRecord:
    session: DraftSession


class DraftSessionManager:
    def __init__(
        self,
        assistant: AssistantService,
        *,
        max_sessions: int = MAX_DRAFT_SESSIONS,
        ttl: timedelta = DRAFT_SESSION_TTL,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        if ttl <= timedelta(0):
            raise ValueError("draft-session ttl must be positive")
        self._assistant = assistant
        self._max_sessions = max_sessions
        self._ttl = ttl
        self._records: dict[str, _SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, prompt: str, locale: str) -> DraftSession:
        message = _normalize_message(prompt)
        draft = await self._assistant.draft_conversation(
            user_messages=[message],
            locale=locale,
            previous_draft=None,
        )
        now = _now()
        session = DraftSession(
            session_id=uuid4().hex,
            locale=locale,
            revision=1,
            status=draft.status,
            busy=False,
            turns=_new_turn_pair(message=message, draft=draft, created_at=now),
            draft=draft,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._prune_locked(now)
            self._make_capacity_locked()
            self._records[session.session_id] = _SessionRecord(session=session)
        return session.model_copy(deep=True)

    async def get(self, session_id: str) -> DraftSession:
        async with self._lock:
            now = _now()
            self._prune_locked(now)
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session not found or expired: {session_id}"
                )
            return record.session.model_copy(deep=True)

    async def continue_session(
        self,
        session_id: str,
        *,
        message: str,
        expected_revision: int,
    ) -> DraftSession:
        normalized_message = _normalize_message(message)
        snapshot = await self._begin_turn(
            session_id,
            expected_revision=expected_revision,
        )
        user_messages = [turn.content for turn in snapshot.turns if turn.role == "user"]
        user_messages.append(normalized_message)
        try:
            draft = await self._assistant.draft_conversation(
                user_messages=user_messages,
                locale=snapshot.locale,
                previous_draft=snapshot.draft,
            )
            return await self._finish_turn(
                session_id,
                message=normalized_message,
                draft=draft,
                expected_revision=expected_revision,
            )
        except BaseException:
            await self._abort_turn(
                session_id,
                expected_revision=expected_revision,
            )
            raise

    async def delete(self, session_id: str, *, expected_revision: int) -> None:
        async with self._lock:
            self._prune_locked(_now())
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session not found or expired: {session_id}"
                )
            session = record.session
            if session.busy:
                raise DraftSessionConflictError(
                    "draft session is currently compiling a turn"
                )
            if session.revision != expected_revision:
                raise DraftSessionConflictError(
                    f"draft session revision is {session.revision}, "
                    f"not {expected_revision}"
                )
            del self._records[session_id]

    async def begin_execution(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> DraftSession:
        async with self._lock:
            now = _now()
            self._prune_locked(now)
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session not found or expired: {session_id}"
                )
            session = record.session
            if session.busy:
                raise DraftSessionConflictError(
                    "draft session is currently compiling or submitting"
                )
            if session.revision != expected_revision:
                raise DraftSessionConflictError(
                    f"draft session revision is {session.revision}, "
                    f"not {expected_revision}"
                )
            if session.execution is not None:
                raise DraftSessionConflictError(
                    "draft session has already been confirmed for execution"
                )
            if (
                session.status != "proposal"
                or session.draft.draft_document is None
                or session.draft.validation is None
            ):
                raise DraftSessionConflictError(
                    "only a validated proposal can be confirmed for execution"
                )
            snapshot = session.model_copy(deep=True)
            record.session = session.model_copy(update={"busy": True})
            return snapshot

    async def finish_execution(
        self,
        session_id: str,
        *,
        expected_revision: int,
        workflow_id: str,
        task_id: str,
    ) -> DraftSession:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session disappeared while submitting: {session_id}"
                )
            session = record.session
            if not session.busy or session.revision != expected_revision:
                raise DraftSessionConflictError(
                    "draft session changed while submitting the execution"
                )
            now = _now()
            updated = session.model_copy(
                update={
                    "busy": False,
                    "execution": DraftSessionExecution(
                        workflow_id=workflow_id,
                        task_id=task_id,
                        draft_id=session.draft.provenance.draft_id,
                        confirmed_revision=expected_revision,
                        submitted_at=now,
                    ),
                    "updated_at": now,
                }
            )
            record.session = DraftSession.model_validate(
                updated.model_dump(mode="python")
            )
            return record.session.model_copy(deep=True)

    async def abort_execution(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if (
                record is not None
                and record.session.busy
                and record.session.revision == expected_revision
                and record.session.execution is None
            ):
                record.session = record.session.model_copy(update={"busy": False})

    async def _begin_turn(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> DraftSession:
        async with self._lock:
            now = _now()
            self._prune_locked(now)
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session not found or expired: {session_id}"
                )
            session = record.session
            if session.busy:
                raise DraftSessionConflictError(
                    "draft session is already compiling another turn"
                )
            if session.execution is not None:
                raise DraftSessionConflictError(
                    "draft session has already been confirmed for execution"
                )
            if session.revision != expected_revision:
                raise DraftSessionConflictError(
                    f"draft session revision is {session.revision}, "
                    f"not {expected_revision}"
                )
            if session.revision >= MAX_DRAFT_SESSION_USER_TURNS:
                raise DraftSessionConflictError(
                    "draft session reached the maximum number of user turns"
                )
            snapshot = session.model_copy(deep=True)
            record.session = session.model_copy(update={"busy": True})
            return snapshot

    async def _finish_turn(
        self,
        session_id: str,
        *,
        message: str,
        draft: AssistantDraft,
        expected_revision: int,
    ) -> DraftSession:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise DraftSessionNotFoundError(
                    f"draft session disappeared while compiling: {session_id}"
                )
            session = record.session
            if not session.busy or session.revision != expected_revision:
                raise DraftSessionConflictError(
                    "draft session changed while compiling the current turn"
                )
            now = _now()
            updated = session.model_copy(
                update={
                    "revision": session.revision + 1,
                    "status": draft.status,
                    "busy": False,
                    "turns": [
                        *session.turns,
                        *_new_turn_pair(
                            message=message,
                            draft=draft,
                            created_at=now,
                        ),
                    ],
                    "draft": draft,
                    "updated_at": now,
                }
            )
            record.session = DraftSession.model_validate(
                updated.model_dump(mode="python")
            )
            return record.session.model_copy(deep=True)

    async def _abort_turn(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if (
                record is not None
                and record.session.busy
                and record.session.revision == expected_revision
            ):
                record.session = record.session.model_copy(update={"busy": False})

    def _prune_locked(self, now: datetime) -> None:
        expired = [
            session_id
            for session_id, record in self._records.items()
            if not record.session.busy and now - record.session.updated_at >= self._ttl
        ]
        for session_id in expired:
            del self._records[session_id]

    def _make_capacity_locked(self) -> None:
        if len(self._records) < self._max_sessions:
            return
        inactive = [
            record.session
            for record in self._records.values()
            if not record.session.busy
        ]
        if not inactive:
            raise DraftSessionCapacityError(
                "draft-session capacity is occupied by active compilations"
            )
        oldest = min(inactive, key=lambda session: session.updated_at)
        del self._records[oldest.session_id]


def _normalize_message(message: str) -> str:
    if not isinstance(message, str):
        raise TypeError("draft-session message must be a string")
    content = message.strip()
    if not content:
        raise ValueError("draft-session message must not be empty")
    if len(content) > 4_000:
        raise ValueError("draft-session message must not exceed 4000 characters")
    return content


def _new_turn_pair(
    *,
    message: str,
    draft: AssistantDraft,
    created_at: datetime,
) -> list[DraftSessionTurn]:
    return [
        DraftSessionTurn(
            turn_id=uuid4().hex,
            role="user",
            content=message,
            created_at=created_at,
            draft_id=None,
        ),
        DraftSessionTurn(
            turn_id=uuid4().hex,
            role="assistant",
            content=_assistant_turn_content(draft),
            created_at=created_at,
            draft_id=draft.provenance.draft_id,
        ),
    ]


def _assistant_turn_content(draft: AssistantDraft) -> str:
    if not draft.questions:
        return draft.message
    questions = "\n".join(f"- {question}" for question in draft.questions)
    return f"{draft.message}\n{questions}"


def _now() -> datetime:
    return datetime.now(timezone.utc)
