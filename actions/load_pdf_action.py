import os
import glob
from base_classes import InteractionAction
from PyPDF2 import PdfReader


class LoadPdfAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args: list = None):
        if not args:
            self.tc.run('file_path')
            while True:
                filename = input("Enter PDF filename or pattern (or q to exit): ")
                if filename.lower() == 'q':
                    break
                if self.process_input(filename):
                    break  # Exit the loop if files were processed
        else:
            filename = ' '.join(args)
            self.process_input(filename)
        
        self.tc.run('chat')  # Always return to chat mode after processing

    def process_input(self, input_pattern):
        file_paths = self.resolve_glob_pattern(input_pattern)
        if not file_paths:
            print(f"No PDF files found matching: {input_pattern}")
            return False

        for file_path in file_paths:
            self.process_and_add_pdf(file_path)
        
        return True  # Indicate that files were processed

    def resolve_glob_pattern(self, pattern):
        resolved_path = self.session.utils.fs.resolve_file_path(pattern, extension='.pdf')
        if resolved_path and os.path.isfile(resolved_path):
            return [resolved_path]
        
        # If it's not a single file, treat it as a glob pattern
        glob_pattern = resolved_path if resolved_path else pattern
        if not glob_pattern.endswith('.pdf'):
            glob_pattern += '*.pdf'
        return glob.glob(glob_pattern)

    @staticmethod
    def process_pdf(file_path):
        try:
            pdf = PdfReader(file_path)
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n"
            
            metadata = {}
            if pdf.metadata:
                metadata = {k: str(v) for k, v in pdf.metadata.items()}

            return {
                'name': os.path.basename(file_path),
                'content': text,
                'metadata': metadata
            }
        except Exception as e:
            print(f"Error processing PDF {file_path}: {str(e)}")
            return None

    def process_and_add_pdf(self, file_path):
        pdf_content = self.process_pdf(file_path)
        if pdf_content:
            try:
                self.session.add_context('pdf', pdf_content)
                print(f"PDF content from {file_path} added to context.")
            except Exception as e:
                print(f"Error adding context for {file_path}: {str(e)}")
        else:
            print(f"Failed to process PDF: {file_path}")
