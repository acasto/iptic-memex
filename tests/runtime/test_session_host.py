"""Live session ownership and safe delivery, without network or model timing."""
from threading import Event
import pytest
from core.runtime import RunRequest, InteractionService
from app.session_host import SessionHost


def test_retained_followup_and_idle_delivery(builder):
    host = SessionHost(builder.build(mode='completion'))
    queued = host.enqueue('Selected evidence', source='investigation')
    try:
        first = host.submit(RunRequest(message='First'), parent_run_id='parent').result(2)
        assert any(e.type == 'input_attached' and e.data['id'] == queued for e in first.events)
        second = host.submit(RunRequest(message='Follow up')).result(2)
        assert first.run_id != second.run_id
        chat = host.session.get_context('chat').get('all')
        assert [x['message'] for x in chat if x['role'] == 'user'] == ['First', 'Follow up']
        assert not host.pending()
        with pytest.raises(NotImplementedError):
            host.restore({})
    finally:
        host.close()
    assert host.session._closed


def test_inflight_delivery_waits_and_overlap_is_rejected(builder):
    host = SessionHost(builder.build(mode='completion'))
    entered, release = Event(), Event()
    def provider():
        entered.set()
        assert release.wait(2)
        return 'Done'
    host.session.provider.chat = provider
    handle = host.submit(RunRequest(message='First'))
    try:
        assert entered.wait(2)
        queued = host.enqueue('Later evidence')
        with pytest.raises(RuntimeError, match='active'):
            host.submit(RunRequest(message='Overlap'))
        assert len(host.pending()) == 1
        release.set()
        assert not any(e.type == 'input_attached' for e in handle.result(2).events)
        result = host.submit(RunRequest(message='Next')).result(2)
        assert any(e.data.get('id') == queued for e in result.events)
    finally:
        release.set()
        host.close()


def test_live_question_reply_and_stale_response(builder):
    host = SessionHost(builder.build(mode='completion'))
    asked = Event()
    requests = []
    service = InteractionService(wait=True)
    def observe(event):
        if event.type == 'interaction_requested':
            requests.append(event.data['id'])
            asked.set()
    def prepare(session):
        assert service.ask_choice('Which?', ['a', 'b']) == 'b'
    handle = host.submit(RunRequest(message='Question'), interactions=service,
                         before_run=prepare, on_event=observe)
    try:
        assert asked.wait(2)
        assert handle.snapshot()['state'] == 'waiting_for_input'
        with pytest.raises(ValueError):
            handle.respond(requests[0], 'c')
        handle.respond(requests[0], 'b')
        assert handle.result(2).status == 'completed'
        with pytest.raises(ValueError):
            handle.respond(requests[0], 'a')
    finally:
        host.close()


def test_cancellation_wakes_input_and_observer_failure_remains_visible(builder):
    host = SessionHost(builder.build(mode='completion'))
    asked = Event()
    service = InteractionService(wait=True)
    def observe(event):
        if event.type == 'interaction_requested':
            asked.set()
            raise RuntimeError('Display failed')
    handle = host.submit(RunRequest(message='Task'), interactions=service,
                         before_run=lambda s: service.ask_text('Question'), on_event=observe)
    assert asked.wait(2)
    host.close(timeout=2)
    result = handle.result(2)
    assert result.status == 'cancelled'
    assert any(e.data.get('code') == 'observer_failed' for e in result.events)
    assert host.session._closed


def test_inbox_bounds_and_accounting_precede_delivery(builder):
    from core.runtime.contracts import RunStopped
    host = SessionHost(builder.build(mode='completion'), inbox_limit=1)
    host.enqueue('Keep me')
    with pytest.raises(ValueError, match='full'):
        host.enqueue('Overflow')
    def stop(session, index):
        raise RunStopped('budget', 'No calls left')
    host.session.before_provider_call = stop
    try:
        result = host.submit(RunRequest(message='Task')).result(2)
        assert result.stop_reason == 'budget'
        assert host.pending()[0].text == 'Keep me'
        assert host.session.provider.calls == 0
    finally:
        host.close()


def test_independent_sessions_and_exclusive_host_ownership(builder):
    first = SessionHost(builder.build(mode='completion'))
    second = SessionHost(builder.build(mode='completion'))
    entered, release = Event(), Event()
    def provider():
        entered.set()
        assert release.wait(2)
        return 'First'
    first.session.provider.chat = provider
    handle = first.submit(RunRequest(message='Private first'))
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match='host'):
            SessionHost(first.session)
        result = second.submit(RunRequest(message='Private second')).result(2)
        assert result.status == 'completed'
        assert 'Private first' not in str(second.session.get_context('chat').get('all'))
        with pytest.raises(TimeoutError):
            first.close(timeout=0.01)
        assert not getattr(first.session, '_closed', False)
        with pytest.raises(RuntimeError, match='closed'):
            first.enqueue('Too late')
    finally:
        release.set()
        handle.result(2)
        first.close()
        second.close()
