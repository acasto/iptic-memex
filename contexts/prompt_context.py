from base_classes import InteractionContext
import os


class PromptContext(InteractionContext):
    """Class for managing prompts - simplified for new architecture"""

    def __init__(self, session, content=None, prompt_resolver=None):
        """
        Initialize the prompt context with pre-resolved content
        
        Args:
            session: The current session
            content: Pre-resolved prompt content or prompt name
            prompt_resolver: The PromptResolver instance (passed by ComponentRegistry)
        """
        self.session = session
        self.output = session.utils.output
        self.prompt_resolver = prompt_resolver
        self.prompt = {
            'name': 'resolved',
            'content': ''
        }
        
        # Process the content
        if content is not None:
            self.process_content(content)
        else:
            # Get default prompt if no content provided
            self.get_default_prompt()
        
    def process_content(self, content):
        """
        Process the provided content
        
        Args:
            content: The prompt content to process
        """
        # Handle none/false case
        if isinstance(content, str) and content.lower() in ['none', 'false']:
            self.prompt['name'] = 'none'
            self.prompt['content'] = ''
            return

        # If we have a prompt resolver, use it
        if self.prompt_resolver:
            resolved_content = self.prompt_resolver.resolve(content)
            if resolved_content:
                processed_content = self.process_templates(resolved_content)
                self.prompt['content'] = processed_content
                self.prompt['name'] = content if isinstance(content, str) else 'resolved'
                return

        # Fallback: treat content as direct text
        if isinstance(content, str):
            # Process templates if needed
            processed_content = self.process_templates(content)
            self.prompt['content'] = processed_content
            self.prompt['name'] = 'direct'

    def get_default_prompt(self):
        """Get the default prompt"""
        if self.prompt_resolver:
            default_content = self.prompt_resolver.resolve()
            if default_content:
                self.prompt['content'] = default_content
                self.prompt['name'] = 'default'

    def process_templates(self, content):
        """
        Process any template variables in the prompt content
        
        Args:
            content: The prompt content to process
        Returns:
            Processed content with templates resolved
        """
        # Check if templating is enabled
        template_handlers = self.session.get_option('DEFAULT', 'template_handler', fallback='none')

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

    def get(self):
        """Return the processed prompt"""
        return self.prompt