from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, LlmPromptProfile, User

PROMPT_ROLES = {
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
}
DECISION_AGENT_PROMPT_ROLES = {
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
}
DECISION_THRESHOLD_PATTERN = re.compile(
    r"(?:minimum[_ ]confidence|minimum[_ ]entry[_ ]score|risk[_ ]tolerance(?:[_ ]score)?|"
    r"uncertainty[_ ]tolerance(?:[_ ]ratio)?|momentum[_ ]deterioration[_ ]tolerance(?:[_ ]pct)?|"
    r"drawdown[_ ]tolerance(?:[_ ]pct)?)\s*(?::|=|is|of)?\s*\d",
    re.IGNORECASE,
)
FORBIDDEN_PATTERNS = (
    r"\bapi[ _-]?key\b",
    r"\bcredential(?:s)?\b",
    r"\btotp\b",
    r"\bauthorization\s+header\b",
    r"/run/secrets",
    r"\bshell\s+command\b",
    r"\bsubprocess\b",
    r"\bbroker\s+api\b",
    r"\b(?:place|execute|submit)\s+(?:an?\s+)?order\b",
    r"주문\s*(?:실행|전송|제출)",
    r"증권사\s*api\s*(?:호출|사용)",
)


class LlmPromptError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _content(value: str) -> str:
    normalized = value.strip().replace("\r\n", "\n").replace("\r", "\n")
    if not 20 <= len(normalized) <= 12_000:
        raise LlmPromptError("PROMPT_LENGTH_INVALID")
    if any(ord(char) < 32 and char not in {"\n", "\t"} for char in normalized):
        raise LlmPromptError("PROMPT_CONTROL_CHARACTER_FORBIDDEN")
    return normalized


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def list_prompts(
    db: Session, *, owner_id: str, role: str | None = None
) -> list[LlmPromptProfile]:
    query = select(LlmPromptProfile).where(LlmPromptProfile.owner_id == owner_id)
    if role:
        if role not in PROMPT_ROLES:
            raise LlmPromptError("PROMPT_ROLE_UNSUPPORTED")
        query = query.where(LlmPromptProfile.role == role)
    return list(
        db.scalars(
            query.order_by(LlmPromptProfile.role, LlmPromptProfile.version_number.desc())
        )
    )


def get_prompt(db: Session, *, owner_id: str, prompt_id: str) -> LlmPromptProfile:
    prompt = db.get(LlmPromptProfile, prompt_id)
    if prompt is None or prompt.owner_id != owner_id:
        raise LlmPromptError("PROMPT_NOT_FOUND", 404)
    return prompt


def create_prompt(
    db: Session,
    *,
    user: User,
    role: str,
    system_prompt: str,
    reason: str,
    correlation_id: str,
) -> LlmPromptProfile:
    if role not in PROMPT_ROLES:
        raise LlmPromptError("PROMPT_ROLE_UNSUPPORTED")
    content = _content(system_prompt)
    highest = db.scalar(
        select(func.max(LlmPromptProfile.version_number)).where(
            LlmPromptProfile.owner_id == user.id,
            LlmPromptProfile.role == role,
        )
    )
    number = int(highest or 0) + 1
    prompt = LlmPromptProfile(
        owner_id=user.id,
        role=role,
        version_number=number,
        version_label=f"{role.lower()}-prompt-v{number}",
        system_prompt=content,
        content_hash=_hash(content),
        reason=reason.strip(),
    )
    db.add(prompt)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LlmPromptError("PROMPT_VERSION_CONFLICT", 409) from exc
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="LLM_PROMPT_CREATED",
            target=prompt.id,
            result="SUCCESS",
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"role": role, "version_number": number, "content_hash": prompt.content_hash},
                sort_keys=True,
            ),
        )
    )
    db.commit()
    db.refresh(prompt)
    return prompt


def validate_prompt(
    db: Session,
    *,
    user: User,
    prompt_id: str,
    correlation_id: str,
) -> LlmPromptProfile:
    prompt = get_prompt(db, owner_id=user.id, prompt_id=prompt_id)
    if prompt.state != "DRAFT":
        raise LlmPromptError("PROMPT_STATE_CONFLICT", 409)
    lowered = prompt.system_prompt.casefold()
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS):
        raise LlmPromptError("PROMPT_UNSAFE_INSTRUCTION")
    if prompt.role in DECISION_AGENT_PROMPT_ROLES and DECISION_THRESHOLD_PATTERN.search(lowered):
        raise LlmPromptError("PROMPT_POLICY_THRESHOLD_FORBIDDEN")
    prompt.state = "VALIDATED"
    prompt.validated_at = datetime.now(UTC)
    prompt.version += 1
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="LLM_PROMPT_VALIDATED",
            target=prompt.id,
            result="SUCCESS",
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"role": prompt.role, "content_hash": prompt.content_hash},
                sort_keys=True,
            ),
        )
    )
    db.commit()
    db.refresh(prompt)
    return prompt
