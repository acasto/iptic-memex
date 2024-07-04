from session_handler import InteractionAction


class SetModelAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.get_action('tab_completion')

    def run(self, args: list = None):
        if not args:
            self.tc.run('model')
            while True:
                model = input(f"Enter model name (or q to exit): ")
                if model == 'q':
                    self.tc.run('chat')  # set the completion back to chat mode
                    break
                if model in self.session.list_models():
                    self.session.set_option('model', model)
                    self.tc.run('chat')
                    break
            return

        model_name = ' '.join(args)
        if model_name in self.session.list_models():
            self.session.set_option('model', model_name)
            self.tc.run('chat')  # set the completion back to chat mode
        else:
            print(f"Model {model_name} not found.")
