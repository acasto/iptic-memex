"""Terminal adapters for read-only workspace task observation."""
import time

import click

from workspace.watch import TaskWatch


def register_watch_command(workspace, paths):
    @workspace.command('watch')
    @click.argument('task_id')
    @click.option('--directory', default=None, help='Workspace records directory.')
    @click.option('--tail', type=click.IntRange(0, 1000), default=30, show_default=True,
                  help='Recent activity lines to show before following.')
    @click.option('--once', is_flag=True, help='Show current activity and exit.')
    @click.option('--verbose', is_flag=True, help='Expand recorded excerpts (up to 4000 characters).')
    @click.option('--interval', type=click.FloatRange(min=.2, max=60), default=1.0,
                  help='Polling interval in seconds.')
    @click.pass_context
    def watch(ctx, task_id, directory, tail, once, verbose, interval):
        """Watch a session task's recorded activity; Ctrl-C leaves the task running."""
        try:
            _, store = paths(ctx, directory)
            viewer = TaskWatch(store, task_id, tail=tail, verbose=verbose)
            task = viewer.tasks.read(task_id)
            if task['spec'].get('strategy', {}).get('workflow', 'session') != 'session':
                raise ValueError('Watch currently supports session tasks; use status/report for legacy runs')
            last_heartbeat = time.monotonic()
            while True:
                current = time.monotonic()
                heartbeat = current - last_heartbeat >= 15
                lines, done = viewer.poll(heartbeat=heartbeat)
                for line in lines:
                    click.echo(line)
                if once or done:
                    return
                if heartbeat:
                    last_heartbeat = current
                time.sleep(interval)
        except KeyboardInterrupt:
            click.echo('\nWatcher stopped; task execution was not changed.')
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
