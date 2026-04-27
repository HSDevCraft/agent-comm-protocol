"""
Security & Governance Layer — AIP-style (Agent Identity Protocol)

Covers:
  - JWT-based agent identity tokens (signed, scoped, short-lived)
  - RBAC: agent roles mapped to MCP tool scopes and A2A capabilities
  - Prompt injection defense (input sanitization + schema validation)
  - Agent spoofing mitigation (signature verification, nonce tracking)
  - Audit log (append-only, tamper-evident via chained hashes)
  - Delegation depth enforcement (prevents recursive loops)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


from src._logging import get_logger

logger = get_logger(__name__)

_SIGNING_SECRET = "dev-secret-change-in-production-use-rsa-in-prod"


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    SPECIALIST = "specialist"
    TOOL_CALLER = "tool_caller"
    OBSERVER = "observer"


ROLE_PERMISSIONS: dict[AgentRole, dict[str, Any]] = {
    AgentRole.ORCHESTRATOR: {
        "mcp_scopes": ["search:read", "db:read", "db:write", "files:read", "files:write"],
        "can_delegate": True,
        "can_orchestrate": True,
        "max_delegation_depth": 5,
        "allowed_capabilities": ["*"],
    },
    AgentRole.PLANNER: {
        "mcp_scopes": ["search:read", "db:read"],
        "can_delegate": True,
        "can_orchestrate": False,
        "max_delegation_depth": 3,
        "allowed_capabilities": ["*"],
    },
    AgentRole.SPECIALIST: {
        "mcp_scopes": ["search:read", "db:read"],
        "can_delegate": False,
        "can_orchestrate": False,
        "max_delegation_depth": 1,
        "allowed_capabilities": [],
    },
    AgentRole.TOOL_CALLER: {
        "mcp_scopes": ["search:read"],
        "can_delegate": False,
        "can_orchestrate": False,
        "max_delegation_depth": 0,
        "allowed_capabilities": [],
    },
    AgentRole.OBSERVER: {
        "mcp_scopes": [],
        "can_delegate": False,
        "can_orchestrate": False,
        "max_delegation_depth": 0,
        "allowed_capabilities": [],
    },
}

_PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|above|prior)\s+instructions?",
    r"you\s+are\s+now\s+a?\s*(different|new|evil|unrestricted)",
    r"(system\s+prompt|jailbreak|bypass|override)\s*:",
    r"pretend\s+(you\s+are|to\s+be)",
    r"do\s+anything\s+now",
    r"<\s*(script|iframe|object|embed)\s*>",
    r"(exec|eval|subprocess|os\.system|shell_exec)\s*\(",
]

_COMPILED_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PROMPT_INJECTION_PATTERNS
]


def _hmac_sign(payload: str) -> str:
    return hmac.new(
        _SIGNING_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


@dataclass
class AgentIdentityToken:
    """
    JWT-style identity token for an agent.
    In production: use RS256 with asymmetric keys.
    Here: HMAC-SHA256 for simplicity.
    """
    agent_id: str
    agent_version: str
    role: AgentRole
    capabilities: list[str]
    mcp_scopes: list[str]
    organization: str
    environment: str
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600)
    jti: str = field(default_factory=lambda: f"jti-{uuid.uuid4().hex[:12]}")
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    delegation_allowed: bool = True
    max_delegation_depth: int = 3
    _signature: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self._signature:
            self._signature = _hmac_sign(self._claims_payload())

    def _claims_payload(self) -> str:
        return json.dumps({
            "sub": self.agent_id,
            "ver": self.agent_version,
            "role": self.role.value,
            "caps": self.capabilities,
            "scopes": self.mcp_scopes,
            "org": self.organization,
            "env": self.environment,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "jti": self.jti,
            "nonce": self.nonce,
            "delegation_allowed": self.delegation_allowed,
            "max_delegation_depth": self.max_delegation_depth,
        }, sort_keys=True)

    def is_valid(self) -> bool:
        if time.time() > self.expires_at:
            return False
        expected = _hmac_sign(self._claims_payload())
        return hmac.compare_digest(self._signature, expected)

    def has_scope(self, scope: str) -> bool:
        return scope in self.mcp_scopes

    def has_capability(self, capability: str) -> bool:
        allowed = self.capabilities
        return capability in allowed or "*" in allowed

    def to_bearer_dict(self) -> dict[str, Any]:
        return {
            "header": {"alg": "HS256", "typ": "JWT"},
            "payload": json.loads(self._claims_payload()),
            "signature": self._signature,
        }


class NonceCache:
    """
    Short-lived nonce store to prevent replay attacks.
    Nonces expire after the token TTL window.
    """

    def __init__(self) -> None:
        self._used: dict[str, float] = {}

    def check_and_consume(self, nonce: str, ttl_seconds: float = 3600) -> bool:
        now = time.time()
        self._evict_expired(now, ttl_seconds)
        if nonce in self._used:
            return False
        self._used[nonce] = now
        return True

    def _evict_expired(self, now: float, ttl_seconds: float) -> None:
        expired = [n for n, t in self._used.items() if now - t > ttl_seconds]
        for n in expired:
            del self._used[n]


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: float
    agent_id: str
    action: str
    resource: str
    outcome: str
    details: dict[str, Any]
    prev_hash: str
    entry_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.entry_hash:
            self.entry_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = json.dumps({
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "action": self.action,
            "resource": self.resource,
            "outcome": self.outcome,
            "prev_hash": self.prev_hash,
        }, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:24]


class AuditLog:
    """
    Append-only, tamper-evident audit log.
    Each entry is chained to the previous via prev_hash (blockchain-lite).
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        agent_id: str,
        action: str,
        resource: str,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        prev_hash = self._entries[-1].entry_hash if self._entries else "genesis"
        entry = AuditEntry(
            entry_id=f"audit-{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            agent_id=agent_id,
            action=action,
            resource=resource,
            outcome=outcome,
            details=details or {},
            prev_hash=prev_hash,
        )
        self._entries.append(entry)
        logger.info(
            "audit_log",
            agent=agent_id,
            action=action,
            resource=resource,
            outcome=outcome,
        )
        return entry

    def verify_chain(self) -> bool:
        """Verify the entire chain — any tampered entry breaks the hash chain."""
        for i, entry in enumerate(self._entries):
            expected_hash = entry._compute_hash()
            if entry.entry_hash != expected_hash:
                logger.error("audit_chain_tampered", entry_id=entry.entry_id, index=i)
                return False
            if i > 0:
                expected_prev = self._entries[i - 1].entry_hash
                if entry.prev_hash != expected_prev:
                    logger.error("audit_chain_broken", entry_id=entry.entry_id)
                    return False
        return True

    def query(
        self,
        agent_id: str | None = None,
        action: str | None = None,
        since: float | None = None,
    ) -> list[AuditEntry]:
        results = self._entries
        if agent_id:
            results = [e for e in results if e.agent_id == agent_id]
        if action:
            results = [e for e in results if e.action == action]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return results

    def count(self) -> int:
        return len(self._entries)


class SecurityGateway:
    """
    Central security enforcement point for all agent interactions.

    Validates:
    - Token authenticity and expiry
    - Nonce freshness (replay attack prevention)
    - Required scopes (MCP tool access)
    - Required capabilities (A2A delegation)
    - Delegation depth limits
    - Prompt injection in inputs
    Records every decision to the audit log.
    """

    def __init__(self) -> None:
        self._nonce_cache = NonceCache()
        self.audit_log = AuditLog()
        self._revoked_jtis: set[str] = set()

    def validate_token(self, token: AgentIdentityToken, context: str = "") -> bool:
        if token.jti in self._revoked_jtis:
            self.audit_log.record(
                token.agent_id, "token_validate", context, "rejected",
                {"reason": "token_revoked", "jti": token.jti},
            )
            return False

        if not token.is_valid():
            self.audit_log.record(
                token.agent_id, "token_validate", context, "rejected",
                {"reason": "invalid_or_expired"},
            )
            return False

        if not self._nonce_cache.check_and_consume(token.nonce):
            self.audit_log.record(
                token.agent_id, "token_validate", context, "rejected",
                {"reason": "nonce_reuse_replay_attack", "nonce": token.nonce},
            )
            return False

        self.audit_log.record(token.agent_id, "token_validate", context, "allowed")
        return True

    def authorize_tool(
        self,
        token: AgentIdentityToken,
        tool_name: str,
        required_scope: str,
    ) -> bool:
        if required_scope and not token.has_scope(required_scope):
            self.audit_log.record(
                token.agent_id, "tool_access", tool_name, "denied",
                {"required_scope": required_scope, "available_scopes": token.mcp_scopes},
            )
            logger.warning("tool_access_denied", agent=token.agent_id, tool=tool_name, scope=required_scope)
            return False

        self.audit_log.record(
            token.agent_id, "tool_access", tool_name, "allowed",
            {"scope": required_scope},
        )
        return True

    def authorize_delegation(
        self,
        token: AgentIdentityToken,
        target_capability: str,
        current_depth: int,
    ) -> bool:
        if not token.delegation_allowed:
            self.audit_log.record(
                token.agent_id, "delegation", target_capability, "denied",
                {"reason": "delegation_not_allowed"},
            )
            return False

        if current_depth >= token.max_delegation_depth:
            self.audit_log.record(
                token.agent_id, "delegation", target_capability, "denied",
                {"reason": "max_depth_exceeded", "current": current_depth, "max": token.max_delegation_depth},
            )
            return False

        self.audit_log.record(
            token.agent_id, "delegation", target_capability, "allowed",
            {"depth": current_depth},
        )
        return True

    def sanitize_input(self, text: str, agent_id: str, context: str = "") -> tuple[str, bool]:
        """
        Detect and neutralize prompt injection attempts.
        Returns (sanitized_text, was_clean).
        """
        detected: list[str] = []
        for pattern in _COMPILED_INJECTION_PATTERNS:
            if pattern.search(text):
                detected.append(pattern.pattern[:40])

        if detected:
            self.audit_log.record(
                agent_id, "prompt_injection_detected", context, "blocked",
                {"patterns_matched": detected, "input_length": len(text)},
            )
            logger.warning(
                "prompt_injection_detected",
                agent=agent_id,
                patterns=detected,
                context=context,
            )
            sanitized = text
            for pattern in _COMPILED_INJECTION_PATTERNS:
                sanitized = pattern.sub("[REDACTED]", sanitized)
            return sanitized, False

        return text, True

    def revoke_token(self, jti: str) -> None:
        self._revoked_jtis.add(jti)
        logger.info("token_revoked", jti=jti)

    def issue_token(
        self,
        agent_id: str,
        agent_version: str,
        role: AgentRole,
        capabilities: list[str],
        organization: str = "default",
        environment: str = "production",
        ttl_seconds: int = 3600,
    ) -> AgentIdentityToken:
        """Issue a new identity token for an agent based on its role."""
        perms = ROLE_PERMISSIONS[role]
        token = AgentIdentityToken(
            agent_id=agent_id,
            agent_version=agent_version,
            role=role,
            capabilities=capabilities,
            mcp_scopes=perms["mcp_scopes"],
            organization=organization,
            environment=environment,
            expires_at=time.time() + ttl_seconds,
            delegation_allowed=perms["can_delegate"],
            max_delegation_depth=perms["max_delegation_depth"],
        )
        self.audit_log.record(
            agent_id, "token_issued", f"role:{role.value}", "success",
            {"jti": token.jti, "ttl": ttl_seconds},
        )
        logger.info("token_issued", agent=agent_id, role=role.value, jti=token.jti)
        return token
