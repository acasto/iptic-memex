# Live session lifecycle

`app.session_host.SessionHost` owns an already assembled session for successive
bounded tasks. It calls the existing runtime; it does not implement a research
strategy or an additional provider/tool loop. No CLI behavior changes in this
slice. Workspace and chat integrations will consume the service later.

```python
from app.session_host import SessionHost
from core.runtime import RunRequest

session = builder.build(mode='completion', project_root='/path/to/project')
host = SessionHost(session)
try:
    handle = host.submit(RunRequest(message='Investigate this question'))
    outcome = handle.result(timeout=60)
    host.enqueue('Selected additional evidence', source='research')
    followup = host.submit(RunRequest(message='Reconsider with the new evidence'))
    revised = followup.result(timeout=60)
finally:
    host.close(timeout=10)
```

The host takes exclusive use of its session. Do not independently execute, mutate,
or close that session while hosted. Multiple hosts may run separate sessions
concurrently. Overlapping submissions to one host are rejected. The caller
supplies a resolved RunRequest and optional application lifecycle callbacks;
chat hooks, agent sentinel policies, and strategy decisions are not implicitly
installed by this low-level service. Session assembly still controls tool mode.

## Observation and interaction

A handle has an ID immediately, then a runtime run ID once execution begins.
It retains session ID and optional parent_run_id for correlation. snapshot()
reports starting/running/waiting/terminal status and pending input. Existing
ordered RunEvents reach on_event; RunOutcome retains non-delta events. There is
no second unbounded event log. Observer errors are recorded as warnings while
internal state observation continues.

Default interactions end with needs_input. Pass InteractionService(wait=True)
for live waiting, then handle.respond(request_id, value). Validation and stale
response checks use the same runtime interaction service. Cancellation wakes
input waits. A parent cancellation token can be explicitly linked at submission.

The bounded inbox accepts selected text, not permission/configuration changes.
Each item has an ID and source label. Attachment occurs only before a provider
call, after mandatory session accounting, and emits input_attached. Attachment
does not prove the provider processed or understood the input. Inputs arriving
during a final call remain pending for the next run; failed attachment leaves the
item queued. A full inbox rejects new input instead of silently losing it.
Caller-supplied before_call callbacks run after inbox attachment.

## Ownership and recovery limits

result(timeout) is a bounded wait, not a deadline. close(timeout) cancels active
work and waits before closing owned resources. If a provider ignores cooperative
cancellation, close raises TimeoutError and leaves the session open but rejects
new submissions. Call close again after execution stops. Thread execution cannot
guarantee hard termination; workspace process supervision remains responsible
for hard deadlines. Worker threads do not survive process exit.

owns_session=False retains caller cleanup responsibility; exclusive use while
hosted still applies. A host can be closed without losing pending inbox inspection,
but the inbox is not persisted. Durable tasks/questions/dispatch belong to workspace.

Follow-up within a live host retains the actual session, provider object, chat,
and opaque provider continuation fields. It is not equivalent to process-restart
recovery. Existing core/session_persistence.py serializes a selected transcript
and parameter subset, not the full provider object, execution scope, pending tool
stack, or host inbox. SessionHost.restore explicitly raises NotImplementedError.
There is currently no provider for which this service claims durable tool or
provider continuation. Do not replay earlier side effects to reconstruct one.
