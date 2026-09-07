# Generated from src/forward_sdk/_async/services/ai.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Forward AI: a snapshot-grounded assistant.

Ask a question about a network in plain language. Forward answers it by calling
its own tools against one snapshot -- running NQE queries, tracing paths, looking
up devices and interfaces -- so the answer is grounded in the same model a check
would run against, not in a general recollection of networking.

**Unpublished, and gated twice over.** These endpoints are absent from Forward's
published description, and they additionally require the ``AI_ALLOWED``
organization property. An organization without Forward AI answers 403 to every
call here, carrying Forward's own sentence, which arrives as an ordinary
:class:`~forward_sdk.errors.ForwardPermissionError`. The SDK deliberately does
not translate that into a status of its own: a caller wanting to show a degraded
state should read the error, not a second vocabulary invented here.

Answers are produced asynchronously. Starting a chat returns immediately with
the chat in ``PROCESSING``; :meth:`AiConversation.wait` polls until it is ``DONE``,
following the same idiom as an NQE execution.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from forward_sdk._generated.models import AiChat, AiMessage
from forward_sdk._ops import ai as ops
from forward_sdk._sync.services._base import Service
from forward_sdk.errors import ForwardTimeoutError

__all__ = ["AiConversation", "AiService"]

DONE = "DONE"
PROCESSING = "PROCESSING"

#: Answers take tens of seconds, so polling starts slow rather than hammering.
POLL_INTERVAL = 3.0
INITIAL_POLL_INTERVAL = 1.0
DEFAULT_TIMEOUT = 600.0


def _status_of(chat: AiChat | Mapping[str, Any]) -> str:
    value = chat.get("status") if isinstance(chat, Mapping) else chat.status
    return str(value or "")


class AiConversation:
    """A live handle on one conversation, grounded in a single snapshot.

    Named apart from the :class:`~forward_sdk.models.AiChat` model it wraps:
    that is the data Forward returned, this is the thing you poll and ask
    follow-up questions of.

    Obtained from :meth:`AiService.start` or :meth:`AiService.get`.
    """

    def __init__(self, service: AiService, chat: AiChat) -> None:
        self._service = service
        self._chat = chat

    def __repr__(self) -> str:
        return f"<AiChat id={self.id!r} status={self.status!r}>"

    @property
    def id(self) -> str:
        return str(self._chat.id or "")

    @property
    def chat(self) -> AiChat:
        """The chat as last seen, without making a request."""
        return self._chat

    @property
    def status(self) -> str:
        return _status_of(self._chat)

    @property
    def is_done(self) -> bool:
        """Whether the last status seen was terminal, without making a request."""
        return self.status == DONE

    @property
    def snapshot_id(self) -> str | None:
        """The snapshot the answers are grounded in.

        Worth reading: a chat is pinned to one snapshot for its lifetime, so a
        follow-up question is answered against the same model as the first.
        """
        return str(self._chat.snapshot_id) if self._chat.snapshot_id else None

    def refresh(self) -> AiChat:
        """Fetch the chat's current state."""
        payload = self._service._send_json(ops.get_chat(chat_id=self.id))
        self._chat = AiChat.model_validate(payload or {})
        return self._chat

    def wait(
        self, *, poll_interval: float = POLL_INTERVAL, timeout: float | None = DEFAULT_TIMEOUT
    ) -> AiChat:
        """Poll until Forward has finished answering.

        Raises:
            ForwardTimeoutError: ``timeout`` elapsed first. Forward keeps
                working; the handle stays usable.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        interval = min(INITIAL_POLL_INTERVAL, poll_interval)

        if not self.is_done:
            self.refresh()
        while not self.is_done:
            if deadline is not None and time.monotonic() >= deadline:
                raise ForwardTimeoutError(
                    f"Forward AI chat {self.id} was still {self.status or 'pending'} "
                    f"after {timeout}s; it is still working"
                )
            time.sleep(interval)
            interval = min(poll_interval, interval * 2)
            self.refresh()
        return self._chat

    def messages(self, *, since: str | None = None) -> list[AiMessage]:
        """The questions asked and the answers given, oldest first."""
        payload = self._service._send_json(ops.list_messages(chat_id=self.id, since=since))
        rows = (payload or {}).get("messages") or []
        return [AiMessage.model_validate(row) for row in rows]

    def ask(self, prompt: str) -> None:
        """Ask a follow-up question.

        Returns as soon as Forward accepts it. Call :meth:`wait` for the answer.
        """
        self._service._send_json(ops.add_message(chat_id=self.id, prompt=prompt))
        self._chat = self._chat.model_copy(update={"status": PROCESSING})

    def ask_and_wait(
        self, prompt: str, *, timeout: float | None = DEFAULT_TIMEOUT
    ) -> AiMessage | None:
        """Ask a follow-up and return the answer, or ``None`` if there is none."""
        self.ask(prompt)
        self.wait(timeout=timeout)
        messages = self.messages()
        return messages[-1] if messages else None

    def rename(self, name: str) -> AiChat:
        payload = self._service._send_json(ops.rename_chat(chat_id=self.id, name=name))
        self._chat = AiChat.model_validate(payload or {})
        return self._chat

    def delete(self) -> None:
        self._service._send_json(ops.delete_chat(chat_id=self.id))

    def transcript(self, *, format: str = "MARKDOWN") -> str:
        """Render the whole conversation as Markdown, HTML or JSON."""
        response = self._service._transport.send(ops.transcript(chat_id=self.id, format=format))
        return response.text


class AiService(Service):
    """Ask Forward questions about a network in plain language.

    See the module docstring: unpublished, and unavailable to an organization
    without Forward AI, which answers 403.
    """

    def list(self) -> list[AiConversation]:
        """The calling user's chats, most recent first as Forward returns them."""
        payload = self._send_json(ops.list_chats())
        rows = (payload or {}).get("chats") or []
        return [AiConversation(self, AiChat.model_validate(row)) for row in rows]

    def get(self, chat_id: str) -> AiConversation:
        payload = self._send_json(ops.get_chat(chat_id=chat_id))
        return AiConversation(self, AiChat.model_validate(payload or {}))

    def start(
        self,
        prompt: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> AiConversation:
        """Start a chat and ask its first question.

        Returns as soon as Forward accepts the question, with the chat in
        ``PROCESSING``. Call :meth:`AiConversation.wait` for the answer, or use
        :meth:`ask` to do both.

        Note this creates something: a chat is stored against your user until
        deleted.
        """
        payload = self._send_json(
            ops.start_chat(
                network_id=self._network(network_id),
                prompt=prompt,
                snapshot_id=self._snapshot(snapshot_id),
            )
        )
        return AiConversation(self, AiChat.model_validate(payload or {}))

    def ask(
        self,
        prompt: str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> AiMessage | None:
        """Ask one question and wait for the answer.

        The convenience form: start a chat, wait, and return the answer. Use
        :meth:`start` when you want to keep asking follow-ups in the same
        conversation, which keeps them grounded in the same snapshot.
        """
        chat = self.start(prompt, network_id=network_id, snapshot_id=snapshot_id)
        chat.wait(timeout=timeout)
        messages = chat.messages()
        return messages[-1] if messages else None
