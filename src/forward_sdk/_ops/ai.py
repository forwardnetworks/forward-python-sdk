"""Request builders for Forward AI.

Unpublished, and additionally gated: every operation here requires the
``AI_ALLOWED`` organization property, so an organization without Forward AI
answers 403 to all of them. It is also a hosted-service and bring-your-own-model
capability rather than something every deployment has.
"""

from __future__ import annotations

from forward_sdk._ops import RequestSpec, op, spec_for

MARKDOWN_ACCEPT = "text/markdown, text/html;q=0.9, application/json;q=0.8"


@op("getAiChats")
def list_chats() -> RequestSpec:
    """List the calling user's chats."""
    return spec_for("getAiChats")


@op("startAiChat")
def start_chat(*, network_id: str, prompt: str, snapshot_id: str | None = None) -> RequestSpec:
    """Start a chat and ask its first question.

    Not idempotent: retrying after a response would create a second chat.
    """
    return spec_for(
        "startAiChat",
        query={"networkId": network_id, "snapshotId": snapshot_id},
        json={"prompt": prompt},
        idempotent=False,
    )


@op("getAiChat")
def get_chat(*, chat_id: str) -> RequestSpec:
    return spec_for("getAiChat", path_params={"chatId": chat_id})


@op("updateAiChat")
def rename_chat(*, chat_id: str, name: str) -> RequestSpec:
    return spec_for("updateAiChat", path_params={"chatId": chat_id}, json={"name": name})


@op("deleteAiChat")
def delete_chat(*, chat_id: str) -> RequestSpec:
    return spec_for("deleteAiChat", path_params={"chatId": chat_id})


@op("getAiChatMessages")
def list_messages(*, chat_id: str, since: str | None = None) -> RequestSpec:
    """List a chat's messages, optionally only those updated since an instant."""
    return spec_for("getAiChatMessages", path_params={"chatId": chat_id}, query={"since": since})


@op("addAiChatMessage")
def add_message(*, chat_id: str, prompt: str) -> RequestSpec:
    """Ask a follow-up. Accepted immediately and answered asynchronously."""
    return spec_for(
        "addAiChatMessage",
        path_params={"chatId": chat_id},
        json={"prompt": prompt},
        idempotent=False,
    )


@op("getAiChatTranscript")
def transcript(*, chat_id: str, format: str = "MARKDOWN") -> RequestSpec:
    """Export a chat as Markdown, HTML or JSON."""
    return spec_for(
        "getAiChatTranscript",
        path_params={"chatId": chat_id},
        query={"format": format},
        accept=MARKDOWN_ACCEPT,
    )
