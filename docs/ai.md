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

`ask()` also takes `timeout=` (see [Waiting](#waiting)), so it can raise
`ForwardTimeoutError`. No handle comes back then; the message of a timeout from the
wait names the chat id, which `client.ai.get(chat_id)` accepts.

Forward also has four one-shot NQE helpers beside the chat (`generate_nqe_query`,
`summarize_nqe_query`, `ask_nqe_docs`, `suggest_nqe_doc_followups`), all methods on
`client.ai` (see the [reference](reference/ai.md)). They need the same
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
    if error.denial:  # best-effort; None if the wording is not recognised
        print(error.denial.kind, error.denial.detail)  # org_setting AI_ALLOWED
```

The SDK does not give this a status of its own. The server's sentence is the
explanation; `error.denial` only classifies that wording and is best-effort. Its `kind`
is `rbac`, `license`, `license_expired`, `org_setting`, `deployment_setting` or
`authority`, and `detail` is what Forward named, if anything. See
[availability](gating.md).

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

follow_up = chat.ask_and_wait("And what about the return path?")  # AiMessage or None
answer = answer_of(follow_up) if follow_up else None
if answer:
    print(answer.summary)
print(chat.snapshot_id)  # the same snapshot as the first question
```

`ask_and_wait()` returns the last message, not the answer. `chat.ask()` only submits a
follow-up: it returns `None` once Forward accepts it, so call `chat.wait()` yourself.
`chat.latest_answer()` applies `answer_of()` to the last message. To check progress
without waiting for the end, call `chat.refresh()` and read `chat.status` or
`chat.is_done`.

## What Forward did to answer

Each message (an `AiMessage`, not the answer inside it) records the tools Forward
called, which is the difference between an answer you can check and one you have to
trust:

```python
for call in message.tool_calls or []:
    print(call.type)  # GET_PATHS, NQE, device lookups, and so on
```

An answer may also decline: `out_of_scope` is set when the question was outside
what Forward AI will address. That is an answer, not an error. When a message has
neither `final_answer` nor `answer` but carries an older Forward's `outOfScopeReason`
string, `answer_of()` returns an answer with `out_of_scope` set and the string as its
`summary`; the message's `final_answer` and `answer` stay `None`.

## Managing chats

```python
client.ai.list()  # your chats, as live conversation handles
chat = client.ai.get(chat_id)
chat.rename("ACL investigation")
print(chat.transcript())  # raw response text; Markdown by default, or format="HTML" / "JSON"
chat.delete()
```

Starting a chat creates something that persists against your user until deleted.

## Waiting

`wait(poll_interval=3.0, timeout=600.0)` polls until the chat is `DONE`, backing off
from one second to three, and raises `ForwardTimeoutError` if your deadline passes first
(`timeout=None` waits forever; the deadline is checked between polls). `client.ai.ask()`
and `chat.ask_and_wait()` take the same `timeout=` (default 600 seconds) for their wait.
`DONE` is the only end state it knows: a chat that stops in any other state is polled
until the timeout. A timeout does not cancel anything: Forward keeps working, and a
handle you hold stays usable. While that chat is still answering, new questions are
refused as described below.

## One question at a time

Forward answers one question per user at a time. While any of your chats is
still answering, a new question -- a new chat or a follow-up in any chat -- is
refused with a 429 naming the busy one:

```
Please wait until 'conversation-212' is done processing
```

The question was refused, not queued, so asking again cannot leave a second
chat behind. The SDK does not treat asking a question as safe to repeat, so the
transport retries its 429 only when it is Forward's rate-limit lockout, not this
refusal. Opt in with
`busy_timeout`, in seconds:

```python
chat = client.ai.start(prompt, busy_timeout=300)  # also ai.ask(), chat.ask(), chat.ask_and_wait()
```

It waits for `Retry-After` between attempts, or five seconds when Forward sends none (or
0), but never longer than the time left in `busy_timeout`. It raises the last
`ForwardRateLimitError` once the time is up. It retries on any 429 from that call, not
only the busy one, and covers only submitting the question, not the `wait()` that
follows. The default, `0`, raises at once, as before. Anything that asks while something
else might be asking -- a webhook hook, a second script, a dashboard -- needs it.

## Source

- `src/forward_sdk/_async/services/ai.py`
- `src/forward_sdk/_sync/services/ai.py`
- `src/forward_sdk/errors.py`
