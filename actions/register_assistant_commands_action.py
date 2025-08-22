from base_classes import InteractionAction


class RegisterAssistantCommandsAction(InteractionAction):
    """
    Default implementation for registering custom assistant commands.
    Users can copy this to their user actions directory and modify it to add custom commands.

    How it works
    - Return a dict mapping ASSISTANT COMMAND KEYS (e.g., "MY_TOOL", "FILE") to configs.
    - Your returned dict is shallow-merged into the built‑ins defined by
      actions/assistant_commands_action.py. That means you can override just the
      fields you care about (e.g., description or schema) without copying the rest.

    Supported fields per command
    - args: list of argument names used by the textual parser. Keep it in sync with the
      arguments your tool accepts so the pseudo‑tool block parser can extract them.
    - function: {"type": "action"|"method", "name": str}
      • For tools, "type" is usually "action" and "name" is the action name without the _action.py suffix.
      • "method" on AssistantCommandsAction is also supported but uncommon for user tools.
    - auto_submit: bool. If True and TOOLS.allow_auto_submit is enabled, the runner will
      immediately take a follow‑up turn after the tool finishes.
    - description: short text shown to providers for official function tools.
    - required: list of required argument keys for official tool schemas.
    - schema: JSON‑schema fragment to describe properties for official tools.
      Provide: { "properties": { key: {type, description, ...}, ... } }

    Notes for official tool calling
    - Providers build function/tool schemas from these entries via get_tool_specs().
    - For OpenAI Responses, all properties become required; the provider sets strict mode
      and additionalProperties=false. Convenience key 'content' is removed automatically.
    - Keep args consistent with your schema so both text parsing and official tools align.

    Minimal examples
    1) Add a brand new tool:
        return {
            "MY_TOOL": {
                "args": ["foo", "bar"],
                "auto_submit": True,
                "function": {"type": "action", "name": "my_tool"},
                "description": "Do something useful with foo and bar.",
                "required": ["foo"],
                "schema": {
                    "properties": {
                        "foo": {"type": "string", "description": "Primary input."},
                        "bar": {"type": "string", "description": "Optional tweak."},
                        "content": {"type": "string", "description": "Optional large freeform input."}
                    }
                }
            }
        }

    2) Partially override an existing command (e.g., FILE) without replacing it:
        return {
            "FILE": {
                "description": "Filesystem operations with safety guards.",
                "schema": {
                    "properties": {
                        "recursive": {"type": "boolean", "description": "Allow recursive delete."}
                    }
                }
            }
        }
    """

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Return a dictionary of custom commands to register.

        Returns:
            dict: Mapping of command names to command configurations.
                 Example format:
                 {
                     "COMMAND": {
                         "args": ["arg1", "arg2"],
                         "auto_submit": True,
                         "function": {
                             "type": "action",
                             "name": "action_name"
                         }
                     }
                 }
        """
        return {}
