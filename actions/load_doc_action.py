import os
from session_handler import InteractionAction
from docx import Document

class LoadDocAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.get_action('tab_completion')

    def run(self, args: list = None):
        if not args:
            self.tc.run('file_path')
            while True:
                filename = input("Enter doc filename (or q to exit): ")
                if filename.lower() == 'q':
                    break
                if self.process_doc(filename):
                    break
        else:
            filename = ' '.join(args)
            self.process_doc(filename)
        
        self.tc.run('chat')

    def process_doc(self, file_path):
        try:
            doc = Document(file_path)
            full_text = "\n".join([para.text for para in doc.paragraphs])
            
            self.session.add_context('doc', {
                'name': os.path.basename(file_path),
                'content': full_text,
                'metadata': {'original_file': file_path}
            })
            
            print(f"Content from {file_path} added to context.")
            return True
        except Exception as e:
            print(f"Error processing document {file_path}: {str(e)}")
            return False
