from session_handler import InteractionAction
import os


class AssistantFileToolAction(InteractionAction):
    """
    Action for handling file operations with different modes:
    - read: Read file into context
    - write: Write content to file
    - append: Append content to file
    - summary: Run command through subprocess on file
    """
    def __init__(self, session):
        self.session = session
        self.fs_handler = session.get_action('assistant_fs_handler')
        self.token_counter = session.get_action('count_tokens')

    def run(self, args: dict, content: str = ""):
        mode = args.get('mode', '').lower()
        filename = args.get('file', '')

        if not filename:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': 'No filename provided'
            })
            return

        if mode == 'read':
            self._handle_read(filename)
        elif mode == 'write':
            self._handle_write(filename, content)
        elif mode == 'append':
            self._handle_append(filename, content)
        elif mode == 'summarize':
            self._handle_summary(filename)
        elif mode == 'delete':
            recursive = args.get('recursive', '').lower() == 'true'
            self._handle_delete(filename, recursive)
        elif mode == 'rename':
            new_name = args.get('new_name')
            if not new_name:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': 'New name required for rename operation'
                })
                return
            self._handle_rename(filename, new_name)
        elif mode == 'copy':
            new_name = args.get('new_name')
            if not new_name:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': 'New name required for copy operation'
                })
                return
            self._handle_copy(filename, new_name)
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Invalid mode: {mode}'
            })

    def _handle_read(self, filename):
        content = self.fs_handler.read_file(filename)
        if content is not None:
            # Check against token limit
            token_count = self.token_counter.count_tiktoken(content)
            max_input = self.session.conf.get_option('TOOLS', 'max_input', fallback=4000)

            if token_count > max_input:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': f'File exceeds maximum token limit ({max_input}). Consider using head/tail commands instead.'
                })
                return

            self.session.add_context('file', filename)
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to read file: {filename}'
            })

    def _handle_write(self, filename, content):
        success = self.fs_handler.write_file(filename, content, create_dirs=True)
        msg = 'File written successfully' if success else f'Failed to write file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_append(self, filename, content):
        success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True)
        msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_summary(self, filename):
        """Handle file summary using AssistantCompletionAction"""
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return

        try:
            # Get file content
            content = self.fs_handler.read_file(resolved_path)
            if content is None:
                return

            # Get configuration
            summary_prompt = self.session.conf.get_option('TOOLS', 'summary_prompt')
            summary_model = self.session.conf.get_option('TOOLS', 'summary_model')

            if not summary_prompt:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': 'No summary prompt configured'
                })
                return

            # Use completion action for summary
            completion = self.session.get_action('assistant_completion')
            summary = completion.run(
                content=content,
                prompt=summary_prompt,
                model=summary_model
            )

            if summary:
                self.session.add_context('assistant', {
                    'name': f'Summary of: {filename}',
                    'content': summary
                })

        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to get file summary: {str(e)}'
            })

    def _handle_delete(self, filename, recursive=False):
        """Handle file or directory deletion"""
        if os.path.isdir(filename):
            success = self.fs_handler.delete_directory(filename, recursive)
        else:
            success = self.fs_handler.delete_file(filename)

        msg = 'Delete operation successful' if success else f'Failed to delete: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_rename(self, old_name, new_name):
        """Handle file or directory rename"""
        success = self.fs_handler.rename(old_name, new_name)
        msg = 'Rename operation successful' if success else f'Failed to rename {old_name} to {new_name}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_copy(self, filename, new_name):
        """Handle file or directory copy"""
        success = self.fs_handler.copy(filename, new_name)
        msg = 'Copy operation successful' if success else f'Failed to copy {filename} to {new_name}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })
