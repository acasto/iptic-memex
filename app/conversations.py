"""Durable chat operations for CLI clients and in-process model consultations."""

from contextlib import closing
import os

from filelock import Timeout

from app.chat import run_chat_turn
from app.project import bind_project, project_options
from app.run_helpers import child_options
from component_registry import ComponentRegistry
from core.cancellation import CancellationToken
from core.session_persistence import (
    load_session_data, prune_sessions, retain_session_lock, save_session,
    saved_params, serialize_session, session_lock, session_path,
)
from session import Session


def _empty_session(builder, options):
    project, options, sources = project_options(options, interactive=True)
    config = builder.config_manager.create_session_config(options)
    if options.get('model') and not config.valid_model(options['model']):
        raise ValueError(f"Unknown model '{options['model']}'.")
    session = Session(config, ComponentRegistry(config))
    bind_project(session, project, sources)
    session.session_origin = 'programmatic'
    return session


def create_conversation(builder, options, *, title=None):
    """Create an empty programmatic conversation without initializing a provider."""
    with closing(_empty_session(builder, options)) as session:
        save_session(session, title=title)
        prune_sessions(session)
        return session.session_uid


def load_conversation(path, session_id):
    """Read a saved conversation and verify its identity."""
    try:
        data = load_session_data(path)
    except FileNotFoundError as exc:
        raise ValueError(f'Saved session {session_id} was not found.') from exc
    if data.get('id') != session_id:
        raise ValueError('Saved session ID does not match its filename')
    return data


def send_message(builder, session_id, message, options, *, parent=None):
    """Commit one turn under the writer lock. A missing ID starts a new conversation."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError('Message must not be empty.')
    if parent is not None:
        if parent.private:
            raise ValueError('Saved conversations are unavailable in a private session.')
        if session_id == parent.session_uid:
            raise ValueError('Cannot send to the currently executing session.')
    data = None
    if session_id is None:
        with closing(_empty_session(builder, options)) as session:
            data = serialize_session(session)
            session_id = session.session_uid
    config = builder.config_manager.create_session_config()
    path = session_path(config, session_id)
    if data is None and not os.path.isfile(path):
        raise ValueError(f'Saved session {session_id} was not found.')
    try:
        with session_lock(path) as lock:
            if data is None:
                data = load_conversation(path, session_id)
            if data.get('kind', 'session') != 'session':
                raise ValueError('Resume the checkpoint and save its fork before sending.')
            token = None
            original_params = None
            if parent is not None:
                # Validate the saved model against the caller's ceiling before assembly.
                original_params = saved_params(data)
                model = original_params.get('model')
                options = child_options(parent, {'model': model} if model else {})
                parent_token = parent.get_cancellation_token()
                if parent_token is not None:
                    token = CancellationToken()
                    parent_token.register_cleanup(lambda: token.cancel(parent_token.reason()))
            with closing(builder.build(mode='session', saved_session=data, **options)) as live:
                retain_session_lock(live, lock)
                result = run_chat_turn(live, message, cancellation=token)
                # Caller scope applies to this turn only, including its sandbox root.
                save_session(live, params=original_params)
                prune_sessions(live)
                return session_id, result
    except Timeout as exc:
        raise ValueError('Session is in use by another writer.') from exc
