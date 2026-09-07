"""Ask Forward a question about a network in plain language.

Forward AI is not part of every deployment, so this shows the refusal path too.
"""

from __future__ import annotations

import sys

from forward_sdk import ForwardClient, ForwardPermissionError

QUESTION = "Which devices have interfaces that are down but not administratively disabled?"


def main(question: str = QUESTION) -> None:
    with ForwardClient.from_env() as client:
        try:
            chat = client.ai.start(question)
        except ForwardPermissionError as error:
            detail = error.error_info.message if error.error_info else str(error)
            print(f"Forward AI is not available here: {detail}")
            return

        print(f"asked, chat {chat.id}, grounded in snapshot {chat.snapshot_id}")
        chat.wait()

        for message in chat.messages():
            answer = message.answer
            print(f"\nQ: {message.prompt}")
            if answer is None:
                print("   (no answer)")
                continue
            print(f"A: {answer.summary}")
            for insight in answer.key_insights or []:
                print(f"   - {insight}")
            tools = [str(call.type) for call in message.tool_calls or []]
            if tools:
                print(f"   (answered using {', '.join(tools)})")


if __name__ == "__main__":
    main(*sys.argv[1:])
