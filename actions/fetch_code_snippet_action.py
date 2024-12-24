import ast
import os
from session_handler import InteractionAction


class FetchCodeSnippetAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.token_counter = session.get_action('count_tokens')

    def run(self, args=None):
        self.tc.run('file_path')
        while True:
            filename = input("Enter Python filename (or q to exit): ")
            if filename.lower() == 'q':
                self.tc.run('chat')
                break

            if not os.path.isfile(filename):
                print(f"File {filename} not found.")
                continue

            _, file_extension = os.path.splitext(filename)

            if file_extension.lower() == '.py':
                self.process_python_file(filename)
            else:
                print(f"Unsupported file type: {file_extension}. Please select a Python (.py) file.")

            self.tc.run('chat')
            break

    def process_python_file(self, filename):
        with open(filename, 'r') as file:
            content = file.read()

        try:
            tree = ast.parse(content)
            code_elements = self.extract_python_elements(tree)
            self.display_and_select_elements(code_elements, filename, content)
        except SyntaxError as e:
            print(f"Error parsing Python file: {str(e)}")

    # noinspection PyTypeChecker
    @staticmethod
    def extract_python_elements(tree):
        elements = [{'type': 'entire_file', 'name': 'Entire File', 'node': tree}]
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                elements.append({'type': 'class', 'name': node.name, 'node': node})
                for sub_node in ast.walk(node):
                    if isinstance(sub_node, ast.FunctionDef):
                        elements.append({'type': 'method', 'name': f"{node.name}.{sub_node.name}", 'node': sub_node})
            elif isinstance(node, ast.FunctionDef):
                elements.append({'type': 'function', 'name': node.name, 'node': node})
        return elements

    def display_and_select_elements(self, elements, filename, full_content):
        while True:
            print(f"\nCode elements in {filename}:")
            for i, element in enumerate(elements, 1):
                print(f"{i}. {element['type'].capitalize()}: {element['name']}")

            choice = input("\nEnter the number of the element to import (or q to quit): ")
            if choice.lower() == 'q':
                break
            try:
                index = int(choice) - 1
                if 0 <= index < len(elements):
                    selected_element = elements[index]
                    if selected_element['type'] == 'entire_file':
                        code_snippet = full_content
                    else:
                        code_snippet = ast.get_source_segment(full_content, selected_element['node'])

                    token_count = self.token_counter.count_tiktoken(code_snippet)
                    print(f"\nToken count for text-only content: {token_count}")

                    print("\nPreview of selected code:")
                    print("-----------------------------")
                    print(code_snippet[:500] + "..." if len(code_snippet) > 500 else code_snippet)
                    print("-----------------------------")

                    confirm = input("Add this code snippet to context? (y/n): ").lower()
                    if confirm == 'y':
                        self.session.add_context('code_snippet', {
                            'name': f"{selected_element['type'].capitalize()}: {selected_element['name']}",
                            'content': code_snippet,
                            'language': 'python'
                        })
                        print(f"Added {selected_element['type']} {selected_element['name']} to context.")
                        break
                    else:
                        print("Code snippet not added. You can select another element.")
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number or 'q'.")

    @staticmethod
    def get_token_count(text):
        # Implement token counting logic here if needed
        # For now, we'll just return the character count as a placeholder
        return len(text)
