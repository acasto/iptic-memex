import os
from session_handler import InteractionAction
from openpyxl import load_workbook
import csv
from io import StringIO

class LoadSheetAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args: list = None):
        if not args:
            self.tc.run('file_path')
            while True:
                filename = input("Enter spreadsheet filename (or q to exit): ")
                if filename.lower() == 'q':
                    break
                if self.process_sheet(filename):
                    break
        else:
            filename = ' '.join(args)
            self.process_sheet(filename)
        
        self.tc.run('chat')

    def process_sheet(self, file_path):
        try:
            wb = load_workbook(filename=file_path, read_only=True)
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                csv_output = StringIO()
                csv_writer = csv.writer(csv_output)
                
                for row in sheet.iter_rows(values_only=True):
                    csv_writer.writerow(row)
                
                csv_content = csv_output.getvalue()
                
                self.session.add_context('sheet', {
                    'name': f"{os.path.basename(file_path)} - {sheet_name}",
                    'content': csv_content,
                    'metadata': {'original_file': file_path, 'sheet_name': sheet_name}
                })
                
                print(f"Sheet '{sheet_name}' from {file_path} added to context.")
            
            return True
        except Exception as e:
            print(f"Error processing spreadsheet {file_path}: {str(e)}")
            return False
