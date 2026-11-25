"""One-shot clients for the same durable conversations used by chat, TUI and Web."""

import json
import sys

import click

from app.conversations import create_conversation, load_conversation, send_message
from core.session_persistence import format_session_description, list_sessions, session_path


def _json(value):
    click.echo(json.dumps(value, ensure_ascii=False))


def _stored_path(config, sid):
    try:
        path = session_path(config, sid)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint='ID') from exc
    return path


def _transcript(ctx, sid, query, last, json_output):
    config = ctx.obj['CONFIG_MANAGER'].create_session_config()
    path = _stored_path(config, sid)
    try:
        data = load_conversation(path, sid)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    rows = []
    needle = query.casefold() if query else ''
    for index, turn in enumerate(data.get('chat', ()), 1):
        message = turn.get('message', turn.get('content')) or ''
        if not isinstance(message, str):
            message = json.dumps(message, ensure_ascii=False)
        if needle in message.casefold():
            rows.append({'index': index, 'role': turn.get('role'), 'text': message})
    if last is not None:
        rows = rows[-last:]
    if json_output:
        _json(rows)
    else:
        for row in rows:
            click.echo(f"[{row['index']}] {row['role']}: {row['text']}")


def register_session_commands(cli, render_result, render_exception):
    """Register the discoverable saved-session interface."""
    @cli.group('session')
    def group():
        """Create, inspect, and continue saved conversations one turn at a time."""

    @group.command('new')
    @click.option('-m', '--model', help='Model for this conversation')
    @click.option('--title', help='Title shown in session listings')
    @click.pass_context
    def new(ctx, model, title):
        """Create a programmatic session and print its ID."""
        options = dict(ctx.obj['OPTIONS'])
        if model:
            options['model'] = model
        try:
            click.echo(create_conversation(ctx.obj['BUILDER'], options, title=title))
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc

    @group.command('list')
    @click.option('--all', 'all_origins', is_flag=True, help='Include both origins')
    @click.option('--origin', type=click.Choice(['interactive', 'programmatic']))
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def listing(ctx, all_origins, origin, json_output):
        """List saved sessions (interactive history by default)."""
        if all_origins and origin:
            raise click.UsageError('Choose either --origin or --all.')
        config = ctx.obj['CONFIG_MANAGER'].create_session_config()
        items = list_sessions(config, origin=None if all_origins else origin or 'interactive')
        if json_output:
            _json(items)
        else:
            for item in items:
                click.echo(format_session_description(item))

    @group.command('read')
    @click.argument('id')
    @click.option('--last', type=click.IntRange(min=1), help='Show the last N messages')
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def read(ctx, id, last, json_output):
        """Print the saved transcript in chronological order."""
        _transcript(ctx, id, None, last, json_output)

    @group.command('search')
    @click.argument('id')
    @click.argument('query')
    @click.option('--last', type=click.IntRange(min=1), help='Show the last N matches')
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def search(ctx, id, query, last, json_output):
        """Find messages containing QUERY (case-insensitive literal text)."""
        _transcript(ctx, id, query, last, json_output)

    @group.command('send')
    @click.argument('id')
    @click.argument('message', required=False)
    @click.option('--stdin-message', is_flag=True, help='Read the message from standard input')
    @click.option('--json', 'json_output', is_flag=True, help='Print the structured run outcome')
    @click.pass_context
    def send(ctx, id, message, stdin_message, json_output):
        """Append one chat turn, save the conversation, and print the reply."""
        if stdin_message == (message is not None):
            raise click.UsageError('Supply either MESSAGE or --stdin-message.')
        message = sys.stdin.read() if stdin_message else message
        if not message.strip():
            raise click.UsageError('Message must not be empty.')
        _stored_path(ctx.obj['CONFIG_MANAGER'].create_session_config(), id)
        try:
            _, result = send_message(ctx.obj['BUILDER'], id, message, ctx.obj['OPTIONS'])
        except Exception as exc:
            render_exception(ctx, exc, json_output=json_output)
        render_result(ctx, result, json_output=json_output)
