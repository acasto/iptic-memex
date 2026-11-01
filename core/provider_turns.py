"""Provider transcript views for tool media, without altering stored history."""


def provider_turns(turns):
    """Deliver tool-created images through existing user-image encoders.

    Tool results remain tool results in domain history. Providers needing images
    in user content get a media-only input after the entire native tool batch,
    preserving the contiguous tool-result messages required by their protocols.
    """
    pending = []

    def media():
        return {'role': 'user', 'message': '', 'context': list(pending),
                'meta': {'kind': 'tool_media'}}

    for turn in turns:
        role = turn.get('role')
        pseudo = (turn.get('meta') or {}).get('kind') == 'tool_result'
        if pending and role != 'tool':
            yield media()
            pending.clear()
        if role == 'tool' or pseudo:
            images = [context for context in turn.get('context') or [] if context.get('type') == 'image']
            pending.extend(images)
            if pseudo and images:
                turn = dict(turn, context=[c for c in turn.get('context') or [] if c.get('type') != 'image'])
        yield turn
    if pending:
        yield media()
