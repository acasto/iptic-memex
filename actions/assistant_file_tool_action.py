from base_classes import InteractionAction
import subprocess
import os
import tempfile


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
        self.memex_runner = session.get_action('memex_runner')

    def run(self, args: dict, content: str = ""):
        mode = args.get('mode', '').lower()
        filename = args.get('file', '')

        if not filename:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': 'No filename provided'
            })
            return

        mode_handlers = {
            'read': self._handle_read,
            'write': lambda f: self._handle_write(f, content),
            'edit': lambda f: self._handle_edit(f, content),
            'append': lambda f: self._handle_append(f, content),
            'summarize': self._handle_summary,
            'delete': lambda f: self._handle_delete(f, args.get('recursive', '').lower() == 'true'),
            'rename': lambda f: self._handle_rename(f, args.get('new_name')),
            'copy': lambda f: self._handle_copy(f, args.get('new_name')),
        }

        handler = mode_handlers.get(mode)
        if handler:
            # For rename and copy, we need to check for new_name before calling the handler
            if mode in ['rename', 'copy'] and not args.get('new_name'):
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': f'New name required for {mode} operation'
                })
                return
            handler(filename)
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Invalid mode: {mode}'
            })

    def _handle_read(self, filename):
        content = self.fs_handler.read_file(filename)
        if content is not None:
            token_count = self.token_counter.count_tiktoken(content)
            limit = int(self.session.get_tools().get('large_input_limit', 4000))

            if token_count > limit:
                if self.session.get_tools().get('confirm_large_input', True):
                    self.session.set_flag('auto_submit', False)
                    self.session.utils.output.write(
                        f"File exceeds token limit ({limit}) for assistant. Auto-submit disabled.")
                else:
                    self.session.add_context('assistant', {
                        'name': 'file_tool_error',
                        'content': f'File exceeds token limit ({limit}). Consider using head/tail commands instead.'
                    })
                    return

            self.session.add_context('file', filename)
        else:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to read file: {filename}'
            })

    def _handle_write(self, filename, content):
        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            # Chat/TUI/Completion default path: prompt/confirm handled in fs_handler
            success = self.fs_handler.write_file(filename, content, create_dirs=True)
            msg = 'File written successfully' if success else f'Failed to write file: {filename}'
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Writes are disabled by policy; output a unified diff of changes you would apply.'
            })
            return

        if policy == 'dry-run':
            # Avoid noisy read errors: only read if file exists
            try:
                resolved = self.fs_handler.resolve_path(filename, must_exist=True)
            except TypeError:
                # Backward compatibility if resolve_path signature differs
                resolved = self.fs_handler.resolve_path(filename)
                if resolved and not os.path.exists(resolved):
                    resolved = None
            if resolved:
                original = self.fs_handler.read_file(filename) or ''
            else:
                original = ''
            try:
                diff_text = self.fs_handler._generate_diff(original, content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': diff_text or 'No changes (dry-run)'
            })
            return

        # allow: perform write non-interactively
        success = self.fs_handler.write_file(filename, content, create_dirs=True, force=True)
        msg = 'File written successfully' if success else f'Failed to write file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_append(self, filename, content):
        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True)
            msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Appends are disabled by policy; output a unified diff of appended changes.'
            })
            return

        if policy == 'dry-run':
            try:
                resolved = self.fs_handler.resolve_path(filename, must_exist=True)
            except TypeError:
                resolved = self.fs_handler.resolve_path(filename)
                if resolved and not os.path.exists(resolved):
                    resolved = None
            if resolved:
                original = self.fs_handler.read_file(filename) or ''
            else:
                original = ''
            # Emulate append with newline handling kept simple
            new_content = original + ('' if original.endswith('\n') or not isinstance(content, str) else '\n') + content
            try:
                diff_text = self.fs_handler._generate_diff(original, new_content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': diff_text or 'No changes (dry-run append)'
            })
            return

        success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=True)
        msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_summary(self, filename):
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return

        try:
            summary_prompt = self.session.get_tools().get('summary_prompt')
            summary_model = self.session.get_tools().get('summary_model')
            if not summary_model:
                self.session.add_context('assistant', {
                    'name': 'file_tool_error',
                    'content': 'No summary model configured'
                })
                return

            result = self.memex_runner.run('-m', summary_model, '-p', summary_prompt, '-f', resolved_path)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            # Check summary against token limit
            token_count = self.token_counter.count_tiktoken(summary)
            limit = int(self.session.get_tools().get('large_input_limit', 4000))

            if token_count > limit:
                if self.session.get_tools().get('confirm_large_input', True):
                    self.session.set_flag('auto_submit', False)
                else:
                    self.session.add_context('assistant', {
                        'name': 'file_tool_error',
                        'content': f'Summary exceeds token limit ({limit}). Try using a different summarization approach.'
                    })
                return

            self.session.add_context('assistant', {
                'name': f'Summary of: {filename}',
                'content': summary
            })
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to get file summary: {str(e)}'
            })

    def _handle_delete(self, filename, recursive=False):
        """Handle file or directory deletion"""
        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            if os.path.isdir(filename):
                success = self.fs_handler.delete_directory(filename, recursive)
            else:
                success = self.fs_handler.delete_file(filename)
            msg = 'Delete operation successful' if success else f'Failed to delete: {filename}'
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Deletes are disabled by policy; describe intended deletion instead.'
            })
            return

        if policy == 'dry-run':
            kind = 'directory' if os.path.isdir(filename) else 'file'
            msg = f"Would delete {kind} {filename}{' recursively' if (kind=='directory' and recursive) else ''}"
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if os.path.isdir(filename):
            success = self.fs_handler.delete_directory(filename, recursive, force=True)
        else:
            success = self.fs_handler.delete_file(filename, force=True)
        msg = 'Delete operation successful' if success else f'Failed to delete: {filename}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_rename(self, old_name, new_name):
        """Handle file or directory rename"""
        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.rename(old_name, new_name)
            msg = 'Rename operation successful' if success else f'Failed to rename {old_name} to {new_name}'
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Renames are disabled by policy; describe intended rename instead.'
            })
            return
        if policy == 'dry-run':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': f'Would rename {old_name} to {new_name}'
            })
            return
        success = self.fs_handler.rename(old_name, new_name, force=True)
        msg = 'Rename operation successful' if success else f'Failed to rename {old_name} to {new_name}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_copy(self, filename, new_name):
        """Handle file or directory copy"""
        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.copy(filename, new_name)
            msg = 'Copy operation successful' if success else f'Failed to copy {filename} to {new_name}'
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': msg
            })
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Copies are disabled by policy; describe intended copy instead.'
            })
            return
        if policy == 'dry-run':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': f'Would copy {filename} to {new_name}'
            })
            return
        success = self.fs_handler.copy(filename, new_name, force=True)
        msg = 'Copy operation successful' if success else f'Failed to copy {filename} to {new_name}'
        self.session.add_context('assistant', {
            'name': 'file_tool_result',
            'content': msg
        })

    def _handle_edit(self, filename, edit_request):
        """Handle file editing with LLM assistance"""
        original_content = self.fs_handler.read_file(filename)
        if original_content is None:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to read file or empty: {filename}'
            })
            return

        edit_model = self.session.get_tools().get('edit_model')
        if not edit_model:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': 'No edit model configured'
            })
            return

        conversation_history = self._get_formatted_conversation_history()
        prompt_content = self._build_edit_prompt(original_content, conversation_history, edit_request)

        edited_content = self._run_edit_subprocess(edit_model, prompt_content)

        if edited_content is not None:
            self._process_and_confirm_edit(filename, original_content, edited_content)

    def _run_edit_subprocess(self, edit_model, prompt_content):
        """Runs the edit LLM as a subprocess and returns the output."""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
                temp_file.write(prompt_content)
                temp_path = temp_file.name

            result = self.memex_runner.run('-m', edit_model, '-f', temp_path, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Edit LLM failed: {e.stderr}'
            })
            return None
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'An unexpected error occurred during edit subprocess: {str(e)}'
            })
            return None
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _process_and_confirm_edit(self, filename, original_content, edited_content):
        """Applies the edit by writing the new content to the file."""
        if not edited_content:
            return

        # Check if there are actually changes
        if original_content == edited_content:
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'No changes detected in edited file'
            })
            return

        agent_mode = self.session.in_agent_mode()
        policy = self.session.get_agent_write_policy()
        if policy is None:
            # Default path uses confirmation via fs_handler
            self._handle_write(filename, edited_content)
            return

        if policy == 'deny':
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': 'Writes are disabled by policy; output a unified diff of the edit you would apply.'
            })
            return
        if policy == 'dry-run':
            try:
                diff_text = self.fs_handler._generate_diff(original_content, edited_content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {
                'name': 'file_tool_result',
                'content': diff_text or 'No changes (dry-run edit)'
            })
            return

        # allow
        self._handle_write(filename, edited_content)

    def _get_formatted_conversation_history(self):
        """Get the last few turns of conversation history, formatted for the prompt."""
        chat_context = self.session.get_context('chat')
        if not chat_context:
            return "## Conversation History\nNo conversation history available."

        # Get the last 4 messages
        history = chat_context.get('all')[-4:]
        if not history:
            return "## Conversation History\nNo conversation history available."

        # Format the conversation history
        formatted_history = ['## Conversation History']
        for msg in history:
            role = msg.get('role', 'unknown').capitalize()
            message = msg.get('message', '')
            formatted_history.append(f"[{role}]: {message}")

        return '\n'.join(formatted_history)

    @staticmethod
    def _build_edit_prompt(original_content, conversation_history, edit_request):
        """Build the prompt content for the edit LLM"""

        # Define prompt sections
        intro_section = """You are a code editor. Your task is to apply the requested changes to the provided file and output the complete modified file.

    You will receive:
    1. The content of the original file.
    2. A few turns of conversation history between the user and the assistant to provide some context for the requested changes.
    3. The requested changes to be applied to the oringinal file."""

        original_file_section = """=== ORIGINAL FILE ===
    {original_content}
    === END ORIGINAL FILE ==="""

        conversation_section = """=== CONVERSATION CONTEXT ===
    {conversation_history}
    === END CONVERSATION CONTEXT ==="""

        changes_section = """=== REQUESTED CHANGES ===
    {edit_request}
    === END REQUESTED CHANGES ==="""

        instructions_section = """=== INSTRUCTIONS ===
    Output ONLY the complete modified file with the requested changes applied. Do not include any explanations, comments about the changes, or additional formatting or adjustments. Be sure to preserve original whitespace, newlines, and indentation. Just return the raw file content with edits applied."""

        # Assemble the final prompt
        prompt_parts = [
            intro_section,
            original_file_section,
            # conversation_section,
            changes_section,
            instructions_section
        ]

        prompt = "\n\n".join(prompt_parts)

        return prompt.format(
            original_content=original_content,
            conversation_history=conversation_history,
            edit_request=edit_request
        )
