from __future__ import annotations

import json

from agent_memory_runtime.governance.pii import PiiProtector, SaltedHashPiiVault


def test_pii_vault_tokenizes_payload_and_keeps_only_salted_hashes() -> None:
    vault = SaltedHashPiiVault(secret_key="test-pepper")
    protector = PiiProtector(vault=vault)

    protected = protector.protect_payload(
        {
            "text": "Please refund card 4242 4242 4242 4242 and email a@example.com.",
            "metadata": {"customer_email": "a@example.com"},
        },
        owner_id="user-1",
    )

    serialized = json.dumps(protected.payload, sort_keys=True)
    assert "4242 4242 4242 4242" not in serialized
    assert "a@example.com" not in serialized
    assert serialized.count("${PII_") == 3

    tokens = vault.list_tokens(owner_id="user-1")
    assert len(tokens) == 3
    card_token = next(token for token in tokens if token.pii_type == "payment_card")
    assert vault.resolve(card_token.token_id, owner_id="user-1") is None
    assert vault.matches(card_token.token_id, "4242 4242 4242 4242", owner_id="user-1")
    assert not vault.matches(card_token.token_id, "4242 4242 4242 4242", owner_id="user-2")
    assert not vault.matches(card_token.token_id, "4000 0000 0000 0002", owner_id="user-1")
