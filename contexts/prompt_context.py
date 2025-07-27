from session_handler import InteractionContext
import os


class PromptContext(InteractionContext):
    """Class for managing prompts with support for prompt chaining and templating"""

    def __init__(self, session, content=None):
        """
        Initialize the prompt context with pre-resolved content
        
        Args:
            session: The current session
            content: Pre-resolved prompt content from SessionHandler
        """
        self.session = session
        self.output = session.utils.output
        self.prompt = {
            'name': 'resolved',  # Since resolution is now handled upstream
            'content': ''
        }
        
        # Process the content through chains and templates
        if content is not None:
            self.process_content(content)
        
    def resolve_prompt_chain(self, prompt_str, seen=None):
        """
        Recursively resolves prompt chains into flat list of prompts
        
        Args:
            prompt_str: comma-separated prompt string or chain alias
            seen: set of already processed chains to detect cycles
        Returns:
            list of individual prompt names/files
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

    def process_templates(self, content):
        """
        Process any template variables in the prompt content
        
        Args:
            content: The prompt content to process
        Returns:
            Processed content with templates resolved
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

    def process_content(self, content):
        """
        Process the provided content through chains and templates
        
        Args:
            content: The prompt content to process
        """
        # Handle none/false case
        if isinstance(content, str) and content.lower() in ['none', 'false']:
            self.prompt['name'] = 'none'
            self.prompt['content'] = ''
            return

        # Resolve any prompt chains
        resolved_prompts = self.resolve_prompt_chain(content)
        combined_content = []

        for p in resolved_prompts:
            if p.strip():
                # Read file content if it's a file path
                file_path = self.session.conf.resolve_file_path(p, self.session.conf.get_option('DEFAULT', 'prompt_directory'), '.txt')
                if not file_path:
                    file_path = self.session.conf.resolve_file_path(p, self.session.conf.get_option('DEFAULT', 'user_prompt_directory'), '.txt')
                if not file_path:
                    file_path = self.session.conf.resolve_file_path(p)

                if file_path and os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        prompt_text = f.read()
                else:
                    prompt_text = p

                # Process templates in the content
                processed = self.process_templates(prompt_text)
                combined_content.append(processed)

        # Set final prompt content
        if combined_content:
            self.prompt['content'] = '\n\n'.join(combined_content)

    def get(self):
        """Return the processed prompt"""
        return self.prompt
