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
answer = answer_of(message) if message else None
if answer:
    print(answer.summary)
    for insight in answer.key_insights or []:
        print(" -", insight)
```

Read the answer through `answer_of()`. Forward sends it as `finalAnswer`, some
versions use `answer`, and older ones sent a bare `outOfScopeReason` string;
`answer_of` returns whichever is present, and `None` while the chat is still
working. Reading one field directly means seeing `None` on a deployment that
spells it differently.

`ask()` starts a chat, waits for it to finish and returns its last message (an
`AiMessage`, or `None` if the chat has none); `answer_of()` reads the answer from it.
Answers take tens of seconds, because Forward is running real queries against the
snapshot. `start()` and `ask()` take `network_id=` and `snapshot_id=` to choose the
snapshot. The chat is left behind on your user, and `ask()` does not hand back the
handle; find it with `client.ai.list()` to delete it.

Forward also has four one-shot NQE helpers beside the chat (`generate_nqe_query`,
`summarize_nqe_query`, `ask_nqe_docs`, `suggest_nqe_doc_followups`). They need the same
`AI_ALLOWED` property, and the query and documentation assists answer 410 on an
organization with `NQE_AI_ASSIST` on (from Forward's source; not seen live).

## Handling an organization without it

```python
from forward_sdk import ForwardPermissionError

try:
    message = client.ai.ask("why is this failing?")
except ForwardPermissionError as error:
    # Forward's own sentence, e.g. "AI_ALLOWED is off for your organization".
    # error_info is None when Forward sent no parsed body.
    print(error.error_info.message if error.error_info else error)
    print(error.denial)  # a best-effort kind, e.g. org_setting; None if not recognised
```

The SDK does not give this a status of its own. The server's sentence is the
explanation; `error.denial` only classifies that wording (rbac, licence, organization
setting and so on), and is best-effort. See [availability](gating.md).

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

Each message (an `AiMessage`, not the answer inside it) records the tools Forward
called, which is the difference between an answer you can check and one you have to
trust:

```python
for call in message.tool_calls or []:
    print(call.type)  # GET_PATHS, NQE, device lookups, and so on
```

An answer may also decline: `out_of_scope` is set when the question was outside
what Forward AI will address. That is an answer, not an error. A bare
`outOfScopeReason` string from an older Forward is turned into an answer with
`out_of_scope` set and the string as its `summary`.

## Managing chats

```python
client.ai.list()  # your chats
chat = client.ai.get(chat_id)
chat.rename("ACL investigation")
print(chat.transcript())  # raw response text; Markdown by default, or format="HTML" / "JSON"
chat.delete()
```

Starting a chat creates something that persists against your user until deleted.

## Waiting

`wait(poll_interval=3.0, timeout=600.0)` polls until the chat is `DONE`, backing off
from one second to three, and raises `ForwardTimeoutError` if your deadline passes
first (`timeout=None` waits forever; the deadline is checked between polls). `DONE` is
the only end state it knows: a chat that stops in any other state is polled until the
timeout. Forward keeps
working when that happens and the handle stays usable, so a timeout is a
decision to stop waiting rather than a cancellation.

## One question at a time

Forward answers one question per user at a time. While any of your chats is
still answering, a new question -- a new chat or a follow-up in any chat -- is
refused with a 429 naming the busy one:

```
Please wait until 'conversation-212' is done processing
```

The question was refused, not queued, so asking again cannot leave a second
chat behind. The transport still does not retry it on its own, because it
does not retry this refusal. Opt in with `busy_timeout`, in seconds:

```python
chat = client.ai.start(prompt, busy_timeout=300)  # also ai.ask(), chat.ask(), chat.ask_and_wait()
```

It retries every five seconds, or after `Retry-After` when Forward sends one,
and raises the last `ForwardRateLimitError` once the time is up. It retries on any
429 from that call, not only the busy one, and covers only submitting the question,
not the `wait()` that follows. The default,
`0`, raises at once, as before. Anything that asks while something else might
be asking -- a webhook hook, a second script, a dashboard -- needs it.

## Source

- `src/forward_sdk/_async/services/ai.py`
- `src/forward_sdk/_sync/services/ai.py`
- `src/forward_sdk/errors.py`
