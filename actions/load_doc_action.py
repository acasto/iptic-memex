import os
from base_classes import StepwiseAction, Completed
from docx import Document


class LoadDocAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def start(self, args=None, content: str = "") -> Completed:
        # If path passed via args, use it; otherwise prompt
        filename = None
        if isinstance(args, (list, tuple)) and args:
            filename = " ".join(str(a) for a in args)
        elif isinstance(args, dict):
            filename = args.get('file') or args.get('path')

        if not filename:
            self.tc.run('file_path')
            filename = self.session.ui.ask_text("Enter doc filename (or q to exit): ")

        if str(filename).strip().lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        ok, name = self._process_doc(str(filename))
        self.tc.run('chat')
        return Completed({'ok': ok, 'file': str(filename), 'name': name})

    def resume(self, state_token: str, response) -> Completed:
        # Expect a filename string on resume
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        filename = str(response or '').strip()
        if not filename or filename.lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})
        ok, name = self._process_doc(filename)
        self.tc.run('chat')
        return Completed({'ok': ok, 'file': filename, 'name': name, 'resumed': True})

    # --- Helpers ---------------------------------------------------------
    def _process_doc(self, file_path: str) -> tuple[bool, str | None]:
        try:
            doc = Document(file_path)
            full_text = "\n".join([para.text for para in doc.paragraphs])
            name = os.path.basename(file_path)
            self.session.add_context('doc', {
                'name': name,
                'content': full_text,
                'metadata': {'original_file': file_path}
            })
            try:
                self.session.ui.emit('status', {'message': f"Content from {file_path} added to context."})
            except Exception:
                pass
            return True, name
        except Exception as e:
            try:
                self.session.ui.emit('error', {'message': f"Error processing document {file_path}: {str(e)}"})
            except Exception:
                pass
            return False, None
