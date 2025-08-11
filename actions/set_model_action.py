from base_classes import InteractionAction


class SetModelAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args: list = None):
        if not args:
            self.tc.run('model')
            while True:
                model = input(f"Enter model name (or q to exit): ")
                if model == 'q':
                    self.tc.run('chat')  # set the completion back to chat mode
                    break
                if model in self.session.list_models():
                    self.session.switch_model(model)
                    self.tc.run('chat')
                    break
            return

        model_name = ' '.join(args)
        if model_name in self.session.list_models():
            self.session.switch_model(model_name)
            self.tc.run('chat')  # set the completion back to chat mode
        else:
            print(f"Model {model_name} not found.")
