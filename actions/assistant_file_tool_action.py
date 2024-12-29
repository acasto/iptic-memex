from session_handler import InteractionAction
import subprocess


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
        self.fs = session.utils.fs
        
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
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Invalid mode: {mode}'
            })

    def _handle_read(self, filename):
        content = self.fs.read_file(filename)
        if content is not None:
            self.session.add_context('file', filename)
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to read file: {filename}'
            })

    def _handle_write(self, filename, content):
        success = self.fs.write_file(filename, content)
        msg = 'File written successfully' if success else f'Failed to write file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_append(self, filename, content):
        if not content.endswith('\n'):
            content += '\n'
        success = self.fs.write_file(filename, content, append=True)
        msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_summary(self, filename):
        try:
            # summary_model = self.session.conf.get_option('TOOLS', 'summary_model', fallback='llama-3b')
            summary_model = self.session.conf.get_option('TOOLS', 'summary_model')
            if not summary_model:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': 'No summary model configured'
                })
                return
            summary_prompt = "Summarize the contents of this file, including key concepts, structures, and components. Be concise."

            # Use shell=True to allow piping
            command = f'echo "{summary_prompt}" | memex -m {summary_model} -f "{filename}" -f -'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            self.session.add_context('assistant', {
                'name': f'Summary of: {filename}',
                'content': summary
            })
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to get file summary: {str(e)}'
            })
