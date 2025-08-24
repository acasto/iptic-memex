import ast
import os
from typing import List, Dict, Tuple
from base_classes import StepwiseAction, Completed
from utils.tool_args import get_str


class FetchCodeSnippetAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.token_counter = session.get_action('count_tokens')

    def start(self, args=None, content: str = "") -> Completed:
        self.tc.run('file_path')
        # Determine filename
        filename = None
        if isinstance(args, (list, tuple)) and args:
            filename = " ".join(str(a) for a in args)
        elif isinstance(args, dict):
            filename = get_str(args, 'file') or get_str(args, 'path')
        if not filename:
            filename = self.session.ui.ask_text("Enter Python filename (or q to exit): ")

        if str(filename).lower().strip() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        if not os.path.isfile(str(filename)):
            try:
                self.session.ui.emit('error', {'message': f"File {filename} not found."})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'not_found', 'file': filename})

        _, ext = os.path.splitext(str(filename))
        if ext.lower() != '.py':
            try:
                self.session.ui.emit('error', {'message': f"Unsupported file type: {ext}. Please select a Python (.py) file."})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'unsupported_type', 'file': filename})

        # Parse file and build element options
        elements, full_content = self._parse_python_elements(str(filename))
        if not elements:
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'no_elements', 'file': filename})

        # If only entire file, just add it
        if len(elements) == 1 and elements[0]['key'] == 'entire_file':
            return self._add_snippet(filename, 'entire_file', full_content)

        # Ask user to choose an element
        options = [e['key'] for e in elements]
        choice = self.session.ui.ask_choice(f"Code elements in {os.path.basename(str(filename))}:", options, default=options[0])

        # In blocking path, proceed immediately
        return self._add_snippet(filename, str(choice), full_content)

    def resume(self, state_token: str, response) -> Completed:
        # Response can be filename (first step) or selected key (second step)
        # We detect by checking if response looks like a path vs a key contains ':' or 'entire_file'
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        resp = str(response or '').strip()
        if not resp:
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        # Try to interpret as a selection key first; if it doesn't map, treat as filename
        if ':' in resp or resp == 'entire_file':
            # We need filename from state
            # The web layer provides st.data with original args/content
            filename = None
            try:
                # Attempt to recover filename from stored state
                # api_action_resume passes {'response': ..., 'state': st.data}
                pass
            except Exception:
                filename = None
            # As a fallback, require the user to re-enter filename if state missing
            if not filename:
                filename = self.session.ui.ask_text("Enter Python filename to apply selection:")
            elements, full_content = self._parse_python_elements(str(filename))
            return self._add_snippet(filename, resp, full_content)
        else:
            # Treat as filename; then ask for element selection or add entire file
            filename = resp
            if not os.path.isfile(filename):
                try:
                    self.session.ui.emit('error', {'message': f"File {filename} not found."})
                except Exception:
                    pass
                self.tc.run('chat')
                return Completed({'ok': False, 'error': 'not_found', 'file': filename})
            elements, full_content = self._parse_python_elements(str(filename))
            if not elements:
                self.tc.run('chat')
                return Completed({'ok': False, 'error': 'no_elements', 'file': filename})
            if len(elements) == 1 and elements[0]['key'] == 'entire_file':
                return self._add_snippet(filename, 'entire_file', full_content)
            options = [e['key'] for e in elements]
            choice = self.session.ui.ask_choice(f"Code elements in {os.path.basename(str(filename))}:", options, default=options[0])
            return self._add_snippet(filename, str(choice), full_content)

    # --- Helpers ---------------------------------------------------------
    def _parse_python_elements(self, filename: str) -> Tuple[List[Dict], str]:
        with open(filename, 'r') as f:
            content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            try:
                self.session.ui.emit('error', {'message': f"Error parsing Python file: {str(e)}"})
            except Exception:
                pass
            return [], content
        elements = [{'type': 'entire_file', 'name': 'Entire File', 'key': 'entire_file', 'node': tree}]
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                elements.append({'type': 'class', 'name': node.name, 'key': f'class:{node.name}', 'node': node})
                for sub_node in ast.walk(node):
                    if isinstance(sub_node, ast.FunctionDef):
                        elements.append({'type': 'method', 'name': f"{node.name}.{sub_node.name}", 'key': f'method:{node.name}.{sub_node.name}', 'node': sub_node})
            elif isinstance(node, ast.FunctionDef):
                elements.append({'type': 'function', 'name': node.name, 'key': f'function:{node.name}', 'node': node})
        return elements, content

    def _add_snippet(self, filename: str, key: str, full_content: str) -> Completed:
        # Re-parse to locate the node again and extract source
        elements, content = self._parse_python_elements(str(filename))
        node = None
        chosen = None
        for e in elements:
            if e['key'] == key:
                chosen = e
                node = e['node']
                break
        if not chosen:
            self.tc.run('chat')
            try:
                self.session.ui.emit('error', {'message': f"Selection '{key}' not found."})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'selection_not_found', 'file': filename, 'key': key})

        code_snippet = full_content if key == 'entire_file' else (ast.get_source_segment(content, node) or '')
        tokens = self.token_counter.count_tiktoken(code_snippet)
        try:
            self.session.ui.emit('status', {'message': f"Token count: {tokens}"})
        except Exception:
            pass

        # Add immediately to avoid multi-step state in Web/TUI
        self.session.add_context('code_snippet', {
            'name': f"{chosen['type'].capitalize()}: {chosen['name']}",
            'content': code_snippet,
            'language': 'python'
        })
        try:
            self.session.ui.emit('status', {'message': f"Added {chosen['type']} {chosen['name']} to context."})
        except Exception:
            pass
        self.tc.run('chat')
        return Completed({'ok': True, 'file': filename, 'key': key, 'tokens': tokens})
