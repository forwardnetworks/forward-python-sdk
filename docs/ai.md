# Forward AI

Ask a question about a network in plain language. Forward answers by calling its
own tools against one snapshot: running NQE queries, tracing paths, looking up
devices and interfaces. The answer is grounded in the same model a check would
run against, rather than in a general recollection of how networks behave.

!!! warning "Unpublished, and not available everywhere"

    These endpoints are not in Forward's published API, and they additionally
    require the `AI_ALLOWED` organization property. Forward AI is a
    hosted-service and bring-your-own-model capability, so most organizations
    are refused with a 403 whatever their licence otherwise covers. Handle that
    case; see [availability](gating.md).

## Asking one question

```python
from forward_sdk import answer_of

message = client.ai.ask("Why can nyc-fw01 not reach the database subnet?")
answer = answer_of(message)
print(answer.summary)
for insight in answer.key_insights or []:
    print(" -", insight)
```

Read the answer through `answer_of()`. Forward sends it as `finalAnswer`, some
versions use `answer`, and older ones sent a bare `outOfScopeReason` string;
`answer_of` returns whichever is present, and `None` while the chat is still
working. Reading one field directly means seeing `None` on a deployment that
spells it differently.

`ask()` starts a chat, waits for the answer and returns it. Answers take tens of
seconds, because Forward is running real queries against the snapshot.

## Handling an organization without it

```python
from forward_sdk import ForwardPermissionError

try:
    answer = client.ai.ask("why is this failing?")
except ForwardPermissionError as error:
    # Forward's own sentence, e.g. "AI_ALLOWED is off for your organization".
    print(error.error_info.message)
```

The SDK deliberately does not translate this into a status of its own. A second
vocabulary would be one more thing to keep in step with Forward, and the server's
sentence is more useful than a flag.

## Keeping a conversation

A chat is pinned to one snapshot for its lifetime, so follow-up questions are
answered against the same model as the first. That is what makes a conversation
worth keeping rather than asking each question separately.

```python
chat = client.ai.start("Why can nyc-fw01 not reach the database subnet?")
chat.wait()

for msg in chat.messages():
    answer = answer_of(msg)
    print(msg.prompt, "->", answer.summary if answer else "(pending)")

follow_up = chat.ask_and_wait("And what about the return path?")
print(chat.snapshot_id)  # the same snapshot as the first question
```

## What Forward did to answer

Each message records the tools Forward called, which is the difference between
an answer you can check and one you have to trust:

```python
for call in answer.tool_calls or []:
    print(call.type)  # GET_PATHS, NQE, device lookups, and so on
```

An answer may also decline: `out_of_scope` is set when the question was outside
what Forward AI will address. That is an answer, not an error.

## Managing chats

```python
client.ai.list()  # your chats
chat = client.ai.get(chat_id)
chat.rename("ACL investigation")
print(chat.transcript())  # Markdown, or format="HTML" / "JSON"
chat.delete()
```

Starting a chat creates something that persists against your user until deleted.

## Waiting

`wait()` polls until the chat is `DONE`, backing off from one second to three,
and raises `ForwardTimeoutError` if your deadline passes first. Forward keeps
working when that happens and the handle stays usable, so a timeout is a
decision to stop waiting rather than a cancellation.
