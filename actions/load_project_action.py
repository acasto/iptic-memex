from base_classes import InteractionAction


class LoadProjectAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        while True:

            self.session.get_action('process_contexts').process_contexts_for_user()

            print("1. Add a file")
            print("2. Add a pdf")
            print("3. Add a sheet")
            print("4. Add a doc")
            print("5. Add multiline input")
            print("6. Add web content (Trafilatura)")
            print("7. Add web content (BeautifulSoup)")
            print("8. Add code snippet (Python)")
            print("9. Remove context item")
            # print("7. Done (save project)")
            print()

            selection = input("Choice (or 'q' to quit): ")

            if selection == '1':
                self.session.get_action('load_file').run()
            if selection == '2':
                self.session.get_action('load_pdf').run()
            if selection == '3':
                self.session.get_action('load_sheet').run()
            if selection == '4':
                self.session.get_action('load_doc').run()
            elif selection == '5':
                self.session.get_action('load_multiline').run()
            elif selection == '6':
                self.session.get_action('fetch_from_web').run()
            elif selection == '7':
                self.session.get_action('fetch_from_soup').run()
            elif selection == '8':
                self.session.get_action('fetch_code_snippet').run()
            elif selection == '9':
                self.session.get_action("clear_context").run()
            elif selection.lower() in ['q', 'quit', 'exit']:
                if len(self.session.get_action("process_contexts").get_contexts(self.session)) > 0:
                    print("Do you want to save the project before exiting? (Y/n)")
                    if input().lower == 'n':
                        self.session.get_action("clear_context").run("all")
                        return
                    else:
                        break
                else:
                    return

        project_name = input("Project Name: ")
        project_description = input("Description: ")

        print()
        print("You have created the following project:")
        print()
        print(f"Project Name: {project_name}")
        print(f"Description/Notes: {project_description}")
        self.session.get_action("process_contexts").run("user")

        print("Would you like to save this project? (Y/n)")
        if input().lower() == 'n':
            self.session.get_action("clear_context").run("all")

        self.session.add_context('project', {'name': project_name, 'content': project_description})
        return
