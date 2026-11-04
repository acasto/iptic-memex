"""CLI trigger for one-off schedules; safe to invoke from an external timer."""
import json
import signal

import click

from core.cancellation import CancellationToken
from workspace.schedules import ScheduleStore, Scheduler


def register_schedule_commands(workspace, paths):
    @workspace.group('schedule')
    def schedule():
        """Register one-off task repeats and run due work without a resident service."""

    def selected(ctx, directory):
        if ctx.obj.get('MESSAGE') or ctx.obj.get('FILES') or ctx.obj.get('STDIN_MESSAGE'):
            raise ValueError('Schedules use saved task inputs')
        if set(ctx.obj['OPTIONS']) - {'project_root', 'base_directory'}:
            raise ValueError('Schedules use saved task settings; create a task to change them')
        repo, store = paths(ctx, directory)
        return ScheduleStore(store, repo)

    def output(value):
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))

    @schedule.command('add')
    @click.argument('task_id')
    @click.option('--at', required=True, help='ISO timestamp with explicit offset or Z.')
    @click.option('--directory', default=None)
    @click.pass_context
    def add(ctx, task_id, at, directory):
        """Authorize one unattended execution of a saved directed/session task."""
        try:
            output(selected(ctx, directory).add(task_id, at))
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @schedule.command('list')
    @click.option('--directory', default=None)
    @click.pass_context
    def list_schedules(ctx, directory):
        """Read saved schedules without models or mutations."""
        try:
            output(selected(ctx, directory).list())
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @schedule.command('inspect')
    @click.argument('schedule_id')
    @click.option('--directory', default=None)
    @click.pass_context
    def inspect(ctx, schedule_id, directory):
        """Read the schedule and its occurrence without starting work."""
        try:
            output(selected(ctx, directory).read(schedule_id))
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    def toggle(ctx, schedule_id, directory, enabled):
        try:
            output(selected(ctx, directory).set_enabled(schedule_id, enabled))
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @schedule.command('pause')
    @click.argument('schedule_id')
    @click.option('--directory', default=None)
    @click.pass_context
    def pause(ctx, schedule_id, directory):
        """Prevent a future claim; use task cancellation to stop active work."""
        toggle(ctx, schedule_id, directory, False)

    @schedule.command('resume')
    @click.argument('schedule_id')
    @click.option('--directory', default=None)
    @click.pass_context
    def resume(ctx, schedule_id, directory):
        """Re-enable an unconsumed schedule; never retry an uncertain occurrence."""
        toggle(ctx, schedule_id, directory, True)

    @schedule.command('run-due')
    @click.option('--directory', default=None)
    @click.option('--max-runs', default=1, type=click.IntRange(min=1), show_default=True)
    @click.option('--max-seconds', default=600.0, type=click.FloatRange(min=0, min_open=True),
                  show_default=True)
    @click.option('--dry-run', is_flag=True)
    @click.pass_context
    def run_due(ctx, directory, max_runs, max_seconds, dry_run):
        """Run due occurrences, wait for bounded cleanup, and exit. Output is JSON."""
        token = CancellationToken()
        previous = signal.signal(signal.SIGTERM, lambda *_: token.cancel('Scheduler terminated'))
        try:
            result = Scheduler(selected(ctx, directory)).run_due(
                max_runs=max_runs, max_seconds=max_seconds, dry_run=dry_run, cancellation=token)
            output(result)
            if result['status'] == 'cancelled':
                ctx.exit(130)
            if result['status'] == 'needs_attention':
                ctx.exit(1)
            if result['status'] == 'busy':
                ctx.exit(2)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            signal.signal(signal.SIGTERM, previous)
