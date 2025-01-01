from session_handler import InteractionContext
import sys


class PromptContext(InteractionContext):
    """Class for processing system prompts with support for prompt chaining and templating"""

    def __init__(self, session, prompt=None):
        """
        Initialize the prompt context
        :param prompt: the data to process
        """
        self.session = session
        self.prompt = {}  # dictionary to hold the prompt name and content
        self.output = session.utils.output
        self.proces_prompt(prompt)

    def resolve_prompt_chain(self, prompt_str, seen=None):
        """
        Recursively resolves prompt chains into flat list of prompts
        :param prompt_str: comma-separated prompt string or chain alias
        :param seen: set of already processed chains to detect cycles
        :return: list of individual prompt names/files
        """
        if seen is None:
            seen = set()

        prompts = []
        for p in (p.strip() for p in prompt_str.split(',')):
            if p in seen:
                if self.output:
                    self.output.warning(f"Circular reference detected in prompt chain: {p}")
                continue

            chain = self.session.conf.get_option('PROMPTS', p, fallback=None)
            if chain:
                seen.add(p)
                prompts.extend(self.resolve_prompt_chain(chain, seen))
            else:
                prompts.append(p)

        return prompts

    def _get_prompt_directory(self):
        """Get prompt directory from config"""
        if 'prompt_directory' not in self.session.get_params():
            return self.session.utils.fs.resolve_directory_path(
                self.session.conf.get_option('DEFAULT', 'prompt_directory')
            )
        return self.session.utils.fs.resolve_directory_path(
            self.session.get_params().get('prompt_directory')
        )

    def process_templates(self, content):
        """
        Process any template variables in the prompt content
        :param content: The prompt content to process
        :return: Processed content with templates resolved
        """
        template_handlers = self.session.conf.get_option('DEFAULT', 'template_handler', fallback='none')

        # Return unmodified if templating disabled
        if template_handlers.lower() in ['none', 'false']:
            return content

        # Split handlers and process in order
        result = content
        for handler_name in (h.strip() for h in template_handlers.split(',')):
            if handler_name.lower() == 'default':
                handler_name = 'prompt_template'

            handler = self.session.get_action(handler_name)
            if handler:
                result = handler.run(result)

        return result

    def proces_prompt(self, prompt):
        """
        Process prompts from paths, stdin, chains, or direct strings
        """
        if prompt is not None:
            # Handle none/false case
            if prompt.lower() in ['none', 'false']:
                self.prompt['name'] = 'none'
                self.prompt['content'] = ''
                return

            # Resolve any prompt chains into flat list
            resolved_prompts = self.resolve_prompt_chain(prompt)
            combined_content = []
            prompt_sources = []

            for p in resolved_prompts:
                content = None
                source = None

                # Handle stdin
                if p == '-':
                    content = sys.stdin.read()
                    source = 'stdin'

                # Check prompt directory
                if content is None:
                    prompt_directory = self._get_prompt_directory()
                    prompt_file = self.session.utils.fs.resolve_file_path(p, prompt_directory, '.txt')
                    if prompt_file:
                        with open(prompt_file, 'r') as f:
                            content = f.read()
                            source = prompt_file

                # Check direct file path
                if content is None:
                    prompt_file = self.session.utils.fs.resolve_file_path(p)
                    if prompt_file:
                        with open(prompt_file, 'r') as f:
                            content = f.read()
                            source = prompt_file

                # Treat as direct prompt text
                if content is None and p.strip():
                    content = p
                    source = 'string'

                if content:
                    # Process templates in the content
                    content = self.process_templates(content)
                    combined_content.append(content)
                    prompt_sources.append(source)

            # Set combined prompt info
            if combined_content:
                self.prompt['name'] = ', '.join(str(s) for s in prompt_sources)
                self.prompt['content'] = '\n\n'.join(combined_content)
            else:
                # Fall back to default prompt
                self.prompt['name'] = 'default'
                self.prompt['content'] = self.process_templates(self.session.conf.get_default_prompt())
        else:
            # Use default prompt
            self.prompt['name'] = 'default'
            self.prompt['content'] = self.process_templates(self.session.conf.get_default_prompt())

    def get(self):
        """Return the processed prompt"""
        return self.prompt
