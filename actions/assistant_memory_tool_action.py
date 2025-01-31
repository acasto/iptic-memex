from session_handler import InteractionAction
from utils.storage_utils import TableSchema


class AssistantMemoryToolAction(InteractionAction):
    """
    Supercharged action for handling memory operations via the StorageProvider.
    Supports saving, reading, and clearing memories with optional project scoping.

    Supported actions (passed via the "action" argument):
      - "save": Save a memory. The content comes from the "memory" arg (or fallback to the content parameter)
                and an optional "project" arg may be provided.
      - "read": Read memories. If an "id" is provided, returns that specific record.
                If a "project" is provided:
                  * "all" returns all memories.
                  * Otherwise, returns memories belonging to that project.
                Defaults to reading memories with no project (i.e. default memories).
      - "clear": Clear memories. If an "id" is provided, deletes that specific record.
                If "project" is provided:
                  * "all" clears every memory.
                  * Otherwise, clears all memories for that project.
    """
    def __init__(self, session):
        self.session = session
        # Register the schema for the memories table.
        # The table includes an auto-incrementing ID, memory content, an optional project identifier,
        # and a timestamp that defaults to the current time.
        schema = TableSchema(
            name="memories",
            columns=[
                {"name": "id", "type": "INTEGER PRIMARY KEY AUTOINCREMENT"},
                {"name": "memory", "type": "TEXT"},
                {"name": "project", "type": "TEXT"},
                {"name": "timestamp", "type": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"}
            ],
            indexes=["project", "timestamp"]
        )
        self.session.utils.storage.register_schema(schema)

    def run(self, args: dict, content: str = ""):
        """
        Execute memory operations based on provided arguments.
        """
        action = args.get("action")
        # Determine memory content from either the "memory" argument or the content parameter
        memory_content = args.get("memory", content)
        # Optional project context for this memory operation (could be None)
        project = args.get("project")

        if action == "save":
            if not memory_content:
                self.session.add_context('assistant', {
                    'name': 'assistant_feedback',
                    'content': "No memory content provided for saving."
                })
                return

            # Save the memory record into the "memories" table
            query = "INSERT INTO memories (memory, project) VALUES (?, ?)"
            self.session.utils.storage.provider.execute(query, (memory_content, project))
            self.session.add_context('assistant', {
                'name': 'assistant_feedback',
                'content': "Memory saved successfully."
            })

        elif action == "read":
            memory_id = args.get("id")
            if memory_id:
                # Read a specific memory by its id
                query = "SELECT id, memory, project, timestamp FROM memories WHERE id = ?"
                rows = self.session.utils.storage.provider.execute(query, (memory_id,))
            else:
                # Read memories filtered by project if provided.
                if project:
                    if project.lower() == "all":
                        query = "SELECT id, memory, project, timestamp FROM memories ORDER BY timestamp ASC"
                        rows = self.session.utils.storage.provider.execute(query)
                    else:
                        query = "SELECT id, memory, project, timestamp FROM memories WHERE project = ? ORDER BY timestamp ASC"
                        rows = self.session.utils.storage.provider.execute(query, (project,))
                else:
                    # Default: retrieve memories that have no project assigned (i.e. default memories)
                    query = "SELECT id, memory, project, timestamp FROM memories WHERE project IS NULL ORDER BY timestamp ASC"
                    rows = self.session.utils.storage.provider.execute(query)

            if not rows:
                self.session.add_context('assistant', {
                    'name': 'assistant_feedback',
                    'content': "No memories found for the given criteria."
                })
            else:
                # Format each memory record for output
                output_lines = []
                for mem_id, mem_text, mem_project, mem_timestamp in rows:
                    proj_display = mem_project if mem_project is not None else "default"
                    output_lines.append(
                        f"ID: {mem_id} | Project: {proj_display} | Timestamp: {mem_timestamp}\nMemory: {mem_text}"
                    )
                output_text = "\n\n".join(output_lines)
                self.session.add_context('assistant', {
                    'name': 'assistant_context',
                    'content': output_text
                })

        elif action == "clear":
            memory_id = args.get("id")
            if memory_id:
                # Clear a specific memory by id
                query = "DELETE FROM memories WHERE id = ?"
                self.session.utils.storage.provider.execute(query, (memory_id,))
                self.session.add_context('assistant', {
                    'name': 'assistant_feedback',
                    'content': f"Memory with ID {memory_id} cleared."
                })
            else:
                if project:
                    if project.lower() == "all":
                        # Clear all memories
                        query = "DELETE FROM memories"
                        self.session.utils.storage.provider.execute(query)
                        self.session.add_context('assistant', {
                            'name': 'assistant_feedback',
                            'content': "All memories have been cleared."
                        })
                    else:
                        # Clear all memories for the specified project
                        query = "DELETE FROM memories WHERE project = ?"
                        self.session.utils.storage.provider.execute(query, (project,))
                        self.session.add_context('assistant', {
                            'name': 'assistant_feedback',
                            'content': f"All memories for project '{project}' have been cleared."
                        })
                else:
                    self.session.add_context('assistant', {
                        'name': 'assistant_feedback',
                        'content': "Please specify an 'id' or 'project' to clear memories."
                    })

        else:
            self.session.add_context('assistant', {
                'name': 'assistant_feedback',
                'content': f"Unknown action: {action}"
            })
