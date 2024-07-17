from session_handler import InteractionAction
import subprocess
import shlex


class RunCommandAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        command = input("Enter the command to run (or press Enter to cancel): ")
        if not command.strip():
            print("Command execution cancelled.")
            return

        try:
            result = self.execute_command(command)
            print("\nExecution Result:")
            print(result)

            if self.offer_to_save_output(result):
                return  # Exit the action after saving output
        except Exception as e:
            print(f"An error occurred: {str(e)}")

    def execute_command(self, command):
        try:
            args = shlex.split(command)
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            output = f"Stdout:\n{result.stdout}\n\nStderr:\n{result.stderr}"
            return output
        except subprocess.TimeoutExpired:
            return "Execution timed out after 30 seconds."

    def offer_to_save_output(self, output):
        save_output = input("Do you want to save this output to context? (y/n): ")
        if save_output.lower() == 'y':
            context_name = input("Enter a name for this output context (default: 'Command Output'): ") or "Command Output"
            self.session.add_context('multiline_input', {
                'name': context_name,
                'content': output
            })
            print(f"Output saved to context as '{context_name}'")
            return True
        return False
