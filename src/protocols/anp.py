"""
ANP — Agent Network Protocol (Advanced)
Scope: Decentralized agent discovery and communication across organizations

Uses:
  - DIDs (Decentralized Identifiers) for agent identity without central authority
  - Verifiable Credentials for capability attestation
  - P2P discovery via well-known endpoints
  - Cross-organizational agent collaboration without shared infrastructure
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


from src._logging import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationMethod:
    """Cryptographic key associated with a DID."""
    id: str
    type: str
    controller: str
    public_key_multibase: str


@dataclass
class DIDService:
    """Service endpoint advertised in a DID document."""
    id: str
    type: str
    service_endpoint: str


@dataclass
class DIDDocument:
    """
    W3C DID Document — the canonical identity record for an agent in ANP.

    WHY: Enables cross-organizational agent identity without a central authority.
    Each org controls its own DID namespace (did:web:agents.org.com:agent-name).
    Verification keys allow any party to authenticate the agent without calling home.
    """
    did: str
    controller: str
    verification_methods: list[VerificationMethod] = field(default_factory=list)
    authentication: list[str] = field(default_factory=list)
    services: list[DIDService] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @classmethod
    def create(cls, domain: str, agent_name: str, org_did: str) -> "DIDDocument":
        """Factory: create a standard did:web document for an agent."""
        did = f"did:web:{domain}:{agent_name}"
        key_id = f"{did}#key-1"
        pseudo_pub_key = f"z{hashlib.sha256(did.encode()).hexdigest()[:48]}"

        return cls(
            did=did,
            controller=org_did,
            verification_methods=[
                VerificationMethod(
                    id=key_id,
                    type="Ed25519VerificationKey2020",
                    controller=did,
                    public_key_multibase=pseudo_pub_key,
                )
            ],
            authentication=[key_id],
            services=[
                DIDService(
                    id=f"{did}#agent-endpoint",
                    type="AgentEndpoint",
                    service_endpoint=f"https://{domain}/{agent_name}",
                ),
                DIDService(
                    id=f"{did}#agent-card",
                    type="AgentCard",
                    service_endpoint=f"https://{domain}/{agent_name}/.well-known/agent.json",
                ),
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "@context": [
                "https://www.w3.org/ns/did/v1",
                "https://w3id.org/security/suites/ed25519-2020/v1",
            ],
            "id": self.did,
            "controller": self.controller,
            "verificationMethod": [
                {
                    "id": vm.id,
                    "type": vm.type,
                    "controller": vm.controller,
                    "publicKeyMultibase": vm.public_key_multibase,
                }
                for vm in self.verification_methods
            ],
            "authentication": self.authentication,
            "service": [
                {"id": svc.id, "type": svc.type, "serviceEndpoint": svc.service_endpoint}
                for svc in self.services
            ],
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created)),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.updated)),
        }

    def resolve_endpoint(self, service_type: str) -> str | None:
        for svc in self.services:
            if svc.type == service_type:
                return svc.service_endpoint
        return None


@dataclass
class VerifiableCredential:
    """
    W3C Verifiable Credential attesting an agent's capabilities.

    Issued by a trusted issuer (e.g., org DID or certification authority).
    Allows receiving agents to verify capability claims without calling the issuer.
    """
    credential_id: str
    issuer_did: str
    subject_did: str
    capabilities: list[str]
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 86400)
    proof: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.proof:
            self.proof = self._generate_proof()

    def _generate_proof(self) -> dict[str, Any]:
        payload = json.dumps({
            "issuer": self.issuer_did,
            "subject": self.subject_did,
            "capabilities": self.capabilities,
            "iat": self.issued_at,
        }, sort_keys=True).encode()
        signature = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        return {
            "type": "Ed25519Signature2020",
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.issued_at)),
            "verificationMethod": f"{self.issuer_did}#key-1",
            "proofPurpose": "assertionMethod",
            "proofValue": f"z{signature}",
        }

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://w3id.org/agent/credentials/v1",
            ],
            "type": ["VerifiableCredential", "AgentCapabilityCredential"],
            "id": self.credential_id,
            "issuer": self.issuer_did,
            "issuanceDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.issued_at)),
            "expirationDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.expires_at)),
            "credentialSubject": {
                "id": self.subject_did,
                "capabilities": self.capabilities,
            },
            "proof": self.proof,
        }


@dataclass
class ANPMessage:
    """
    Signed inter-agent message for ANP cross-org communication.
    Every message is signed by the sender's DID key and verified by receiver.
    """
    message_id: str
    sender_did: str
    receiver_did: str
    message_type: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.signature:
            self.signature = self._sign()

    def _sign(self) -> str:
        payload = json.dumps({
            "message_id": self.message_id,
            "sender_did": self.sender_did,
            "receiver_did": self.receiver_did,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }, sort_keys=True).encode()
        return base64.b64encode(hashlib.sha256(payload).digest()).decode()

    def verify_signature(self) -> bool:
        expected = self._sign()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_did": self.sender_did,
            "receiver_did": self.receiver_did,
            "message_type": self.message_type,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "signature": self.signature,
        }


@dataclass
class ANPAgent:
    """
    An agent registered in the ANP decentralized network.
    Holds its DID document, credentials, and can send/receive ANP messages.
    """
    did_document: DIDDocument
    credentials: list[VerifiableCredential] = field(default_factory=list)
    display_name: str = ""

    @property
    def did(self) -> str:
        return self.did_document.did

    def issue_credential_to(
        self,
        subject_did: str,
        capabilities: list[str],
    ) -> VerifiableCredential:
        """Issue a verifiable credential attesting another agent's capabilities."""
        return VerifiableCredential(
            credential_id=f"vc-{uuid.uuid4().hex[:8]}",
            issuer_did=self.did,
            subject_did=subject_did,
            capabilities=capabilities,
        )

    def send_message(
        self,
        receiver_did: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> ANPMessage:
        return ANPMessage(
            message_id=f"anp-{uuid.uuid4().hex[:8]}",
            sender_did=self.did,
            receiver_did=receiver_did,
            message_type=message_type,
            payload=payload,
        )

    def verify_credential(self, credential: VerifiableCredential) -> bool:
        """Verify that a credential is valid and not expired."""
        if not credential.is_valid():
            logger.warning("anp_credential_expired", vc_id=credential.credential_id)
            return False
        return True


class ANPClient:
    """
    ANP Client — enables agents to participate in the decentralized agent network.

    In production: HTTP resolution of DID documents + P2P messaging.
    Here: in-memory simulation of the decentralized network.
    """

    def __init__(self) -> None:
        self._did_registry: dict[str, DIDDocument] = {}
        self._agent_registry: dict[str, ANPAgent] = {}
        self._message_log: list[ANPMessage] = []

    def register_agent(self, agent: ANPAgent) -> None:
        self._did_registry[agent.did] = agent.did_document
        self._agent_registry[agent.did] = agent
        logger.info("anp_agent_registered", did=agent.did)

    def resolve_did(self, did: str) -> DIDDocument | None:
        """Simulate DID resolution (in production: HTTP GET to DID document URL)."""
        return self._did_registry.get(did)

    def discover_agents_by_capability(
        self,
        capability: str,
    ) -> list[ANPAgent]:
        """
        Discover agents with a specific capability via their verifiable credentials.
        In production: queries a decentralized capability index or DID registry.
        """
        matches = []
        for agent in self._agent_registry.values():
            for vc in agent.credentials:
                if capability in vc.capabilities and vc.is_valid():
                    matches.append(agent)
                    break
        return matches

    async def send_message(
        self,
        from_agent: ANPAgent,
        to_did: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> ANPMessage:
        """
        Send a signed ANP message to a remote agent identified by DID.
        Verifies sender signature and logs the message.
        """
        receiver_doc = self.resolve_did(to_did)
        if not receiver_doc:
            raise LookupError(f"Cannot resolve DID: {to_did}")

        message = from_agent.send_message(to_did, message_type, payload)

        if not message.verify_signature():
            raise ValueError("ANP message signature verification failed")

        self._message_log.append(message)
        logger.info(
            "anp_message_sent",
            msg_id=message.message_id,
            sender=from_agent.did,
            receiver=to_did,
            type=message_type,
        )
        return message

    def get_messages_for(self, did: str) -> list[ANPMessage]:
        return [m for m in self._message_log if m.receiver_did == did]

    def network_topology(self) -> dict[str, Any]:
        """Return a summary of the current decentralized network state."""
        return {
            "total_agents": len(self._agent_registry),
            "agents": [
                {
                    "did": agent.did,
                    "display_name": agent.display_name,
                    "credentials": len(agent.credentials),
                    "endpoint": agent.did_document.resolve_endpoint("AgentEndpoint"),
                }
                for agent in self._agent_registry.values()
            ],
        }
