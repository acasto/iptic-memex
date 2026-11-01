"""CLI input collection for the runtime's structured interaction requests."""

import shlex

from core.runtime.interaction import InteractionService


class CLIInteractionHandler:
    def __init__(self, input_handler, output):
        self.input = input_handler
        self.output = output

    def __call__(self, request):
        spec = request.spec
        prompt = spec.get('prompt', '')
        while True:
            if request.kind == 'bool':
                default = spec.get('default')
                suffix = ' [Y/n]' if default is True else ' [y/N]'
                raw = self.input.get_input(prompt=prompt + suffix + ' ', allow_empty=True)
                if not raw.strip():
                    value = bool(default)
                elif raw.strip().lower() in ('yes', 'y', 'true', '1'):
                    value = True
                elif raw.strip().lower() in ('no', 'n', 'false', '0'):
                    value = False
                else:
                    self.output.warning('Please answer yes or no.')
                    continue
            elif request.kind == 'text':
                value = self.input.get_input(prompt=prompt, default=spec.get('default'),
                                             multiline=spec.get('multiline', False))
            elif request.kind == 'choice':
                self.output.write(prompt)
                for index, choice in enumerate(spec['options'], 1):
                    self.output.write(f'  {index}. {choice}')
                raw = self.input.get_input(prompt='Select: ', allow_empty=True)
                parts = [p.strip() for p in raw.split(',')] if spec.get('multi') else [raw.strip()]
                values = [spec['options'][int(p) - 1] if p.isdigit() and 1 <= int(p) <= len(spec['options']) else p
                          for p in parts]
                value = spec.get('default') if not raw.strip() else values if spec.get('multi') else values[0]
            elif request.kind == 'files':
                raw = self.input.get_input(prompt=prompt + ' ', allow_empty=True)
                value = shlex.split(raw)
            else:
                raise ValueError(f'Unsupported interaction kind: {request.kind}')
            try:
                return InteractionService.validate(request, value)
            except ValueError as exc:
                self.output.warning(str(exc))
