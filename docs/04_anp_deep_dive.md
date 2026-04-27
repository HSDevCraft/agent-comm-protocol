# ANP — Agent Network Protocol: Deep Dive

## 1. What Is ANP and Why Does It Exist?

MCP, A2A, and ACP all assume agents share infrastructure — a common registry, a shared message bus, or direct network access. This breaks down when:

- Two different organizations want their agents to collaborate
- An agent needs to discover capabilities it has never seen before
- You want agent identity that isn't controlled by a central authority
- Agents need to prove their capabilities to strangers without calling home

ANP solves the **decentralized, cross-organizational** agent network problem using internet-native standards: W3C DIDs and Verifiable Credentials.

**Analogy**: ANP is TCP/IP for agents. MCP/A2A/ACP handle communication within a known network. ANP is how you reach agents *outside* your network.

---

## 2. Decentralized Identifiers (DIDs)

A DID is a globally unique identifier for an agent that doesn't require a central registry:

```
did:web:agents.acme.com:finance-agent
│    │   │                │
│    │   └─ domain        └─ agent name
│    └─ DID method (did:web = HTTP-resolvable)
└─ DID prefix
```

**DID Methods:**
| Method | Resolution | Use case |
|--------|-----------|---------|
| `did:web` | HTTP GET to well-known URL | Enterprise agents with domains |
| `did:key` | Derive from public key | Ephemeral/test agents |
| `did:ion` | Bitcoin anchored | High-assurance identity |
| `did:peer` | Peer-to-peer | Local/offline agents |

**Resolution**: `did:web:agents.acme.com:finance-agent` resolves to:
```
GET https://agents.acme.com/finance-agent/.well-known/did.json
```

---

## 3. DID Document — Complete Schema

```json
{
  "@context": [
    "https://www.w3.org/ns/did/v1",
    "https://w3id.org/security/suites/ed25519-2020/v1"
  ],
  "id": "did:web:agents.acme.com:finance-agent",
  "controller": "did:web:identity.acme.com",
  "verificationMethod": [
    {
      "id": "did:web:agents.acme.com:finance-agent#key-1",
      "type": "Ed25519VerificationKey2020",
      "controller": "did:web:agents.acme.com:finance-agent",
      "publicKeyMultibase": "z6Mkf5rGMoatrSj1f9iBnSGaLwubGVA4rmSZ4kpwaBQJEb7Q"
    }
  ],
  "authentication": [
    "did:web:agents.acme.com:finance-agent#key-1"
  ],
  "service": [
    {
      "id": "#agent-endpoint",
      "type": "AgentEndpoint",
      "serviceEndpoint": "https://agents.acme.com/finance-agent"
    },
    {
      "id": "#agent-card",
      "type": "AgentCard",
      "serviceEndpoint": "https://agents.acme.com/finance-agent/.well-known/agent.json"
    }
  ],
  "created": "2025-01-01T00:00:00Z",
  "updated": "2025-01-01T00:00:00Z"
}
```

**Key fields:**
- `controller`: Who controls this DID (can update it)
- `verificationMethod`: Cryptographic keys for this agent
- `authentication`: Which keys can authenticate requests from this agent
- `service`: Where to contact this agent (endpoint, agent card)

---

## 4. Verifiable Credentials (VCs)

A Verifiable Credential is a cryptographically signed statement about an agent's capabilities — issued by a trusted party, verified without calling the issuer:

```json
{
  "@context": [
    "https://www.w3.org/2018/credentials/v1",
    "https://w3id.org/agent/credentials/v1"
  ],
  "type": ["VerifiableCredential", "AgentCapabilityCredential"],
  "id": "vc-finance-agent-001",
  "issuer": "did:web:identity.acme.com",
  "issuanceDate": "2025-01-01T00:00:00Z",
  "expirationDate": "2026-01-01T00:00:00Z",
  "credentialSubject": {
    "id": "did:web:agents.acme.com:finance-agent",
    "capabilities": [
      "financial_analysis",
      "risk_assessment",
      "portfolio_optimization"
    ]
  },
  "proof": {
    "type": "Ed25519Signature2020",
    "created": "2025-01-01T00:00:00Z",
    "verificationMethod": "did:web:identity.acme.com#key-1",
    "proofPurpose": "assertionMethod",
    "proofValue": "z5vgB..."
  }
}
```

**Verification flow (no network call to issuer):**
1. Resolve issuer DID → `did:web:identity.acme.com` → get public key
2. Verify `proof.proofValue` using public key
3. Check expiration date
4. Trust the `credentialSubject.capabilities`

---

## 5. ANP Message — Signed Communication

Every ANP message is signed by the sender's private key so recipients can verify authenticity:

```json
{
  "message_id": "anp-e48fe52c",
  "sender_did": "did:web:agents.acme.com:finance-agent",
  "receiver_did": "did:web:agents.partner.org:data-agent",
  "message_type": "CAPABILITY_REQUEST",
  "timestamp": 1735689600.0,
  "payload": {
    "capability": "data_analysis",
    "task": "Analyze ACME Corp Q3 data",
    "requester_org": "acme.com"
  },
  "signature": "H8zPq7..."
}
```

**Signature verification:**
```python
def verify_signature(self) -> bool:
    expected = self._sign()                    # deterministic hash of fields
    return self.signature == expected          # compare
```

In production, use Ed25519:
```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
signature = key.sign(message_bytes)
public_key = key.public_key()
public_key.verify(signature, message_bytes)  # raises InvalidSignature if tampered
```

---

## 6. Cross-Org Discovery Flow

```
Organization A                    Organization B
─────────────────                 ──────────────────────────
PlannerAgent                      DataAgent (did:web:org-b...)
    │                                    │
    │ 1. Needs "data_analysis"           │
    │ 2. Queries ANP network             │
    │                                    │
    │──discover_by_capability────────────►│
    │   (searches VC registry)           │ has VC: "data_analysis"
    │◄──[DataAgent info]─────────────────│
    │                                    │
    │ 3. Resolve DataAgent's DID         │
    │    GET org-b.com/.well-known/      │
    │    did.json → public key           │
    │                                    │
    │ 4. Create ANP message              │
    │    Sign with Org A private key     │
    │                                    │
    │──ANPMessage(signed)────────────────►│
    │                                    │ 5. Verify Org A signature
    │                                    │    via Org A DID
    │                                    │ 6. Process task
    │◄──ANPMessage(signed, result)────────│
    │ 7. Verify Org B signature          │
```

---

## 7. ANP vs Centralized Registry

| Feature | Centralized Registry | ANP |
|---------|---------------------|-----|
| Control | Single authority | No central authority |
| Availability | Single point of failure | Distributed |
| Discovery | Registry lookup | DID resolution + VC |
| Trust | Implicit (registry approved) | Cryptographic (VC) |
| Cross-org | Requires shared registry | Native |
| Revocation | Registry update | VC expiry / DID update |
| Privacy | Registry sees all queries | Can be anonymous |

---

## 8. When to Use ANP

**Use ANP when:**
- Collaborating with agents from different organizations
- Building an open agent marketplace
- Regulatory requirements demand decentralized identity
- You can't trust a central registry

**Use AgentRegistry (A2A) instead when:**
- All agents are within your organization
- You control all agents in the network
- Simplicity is more important than decentralization

---

## 9. ANP Implementation Considerations

**DID Resolution Cache:**
```python
# Cache resolved DID documents (they don't change often)
_did_cache: dict[str, tuple[DIDDocument, float]] = {}

def resolve_did_cached(did: str, ttl=3600) -> DIDDocument:
    if did in _did_cache:
        doc, cached_at = _did_cache[did]
        if time.time() - cached_at < ttl:
            return doc
    doc = http_resolve_did(did)  # GET /.well-known/did.json
    _did_cache[did] = (doc, time.time())
    return doc
```

**VC Validation Checklist:**
1. ✅ Expiration date is in the future
2. ✅ Issuer DID resolves to a valid DID document
3. ✅ Signature verifies with issuer's public key
4. ✅ Credential subject matches the agent presenting it
5. ✅ Capability claims are within expected bounds
