import requests
from session_handler import InteractionAction


class AssistantYoutrackToolAction(InteractionAction):
    """
    Action for interacting with the YouTrack API.
    """

    def __init__(self, session):
        self.session = session
        self.base_url = self.session.conf.get_option('YOUTRACK', 'base_url')
        self.api_key = self.session.conf.get_option('YOUTRACK', 'api_key')

        # Get configurable field mappings and defaults
        self.default_state_filter = self.session.conf.get_option('YOUTRACK', 'default_state_filter',
                                                                 fallback='state:Open')
        self.state_field_name = self.session.conf.get_option('YOUTRACK', 'state_field_name', fallback='State')
        self.priority_field_name = self.session.conf.get_option('YOUTRACK', 'priority_field_name', fallback='Priority')
        self.type_field_name = self.session.conf.get_option('YOUTRACK', 'type_field_name', fallback='Type')
        self.assignee_field_name = self.session.conf.get_option('YOUTRACK', 'assignee_field_name', fallback='Assignee')

        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def run(self, args: dict, content: str = ""):
        mode = args.get('mode', '').lower()

        if mode == 'get_projects':
            self._get_projects(args, content)
        elif mode == 'get_issues':
            self._get_issues(args, content)
        elif mode == 'get_issue_details':
            self._get_issue_details(args, content)
        elif mode == 'create_issue':
            self._create_issue(args, content)
        elif mode == 'update_issue':
            self._update_issue(args, content)
        elif mode == 'assign_issue':
            self._assign_issue(args, content)
        elif mode == 'update_state':
            self._update_state(args, content)
        elif mode == 'update_priority':
            self._update_priority(args, content)
        elif mode == 'update_type':
            self._update_type(args, content)
        elif mode == 'add_comment':
            self._add_comment(args, content)
        else:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Invalid mode: {mode}'
            })

    def _get_projects(self, args, content):
        """Retrieves all projects from YouTrack."""
        url = f'{self.base_url}/api/admin/projects'
        params = {'fields': 'id,name,shortName'}

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            projects = response.json()

            # Format the projects for display with both name and shortName
            formatted_projects = []
            for project in projects:
                formatted_projects.append(
                    f"- {project['name']} (ID: {project['id']}, ShortName: {project['shortName']})"
                )

            self.session.add_context('assistant', {
                'name': 'youtrack_projects',
                'content': '\n'.join(formatted_projects)
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to get projects: {e}'
            })

    def _get_issues(self, args, content):
        """Retrieves issues for a specific project."""
        project_id = args.get('project_id')
        if not project_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Project ID (shortName) is required to get issues.'
            })
            return

        # For queries, we need to use shortName. Handle spaces by wrapping in curly braces
        project_query = f'{{{project_id}}}' if ' ' in project_id else project_id

        query_filter = args.get('query', self.default_state_filter)
        # Build the full query
        if query_filter:
            full_query = f'project:{project_query} {query_filter}'
        else:
            full_query = f'project:{project_query}'

        url = f'{self.base_url}/api/issues'
        params = {
            'query': full_query,
            'fields': 'idReadable,summary,state'
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            issues = response.json()

            # Format the issues for display
            formatted_issues = []
            if issues:
                for issue in issues:
                    state = issue.get('state', {}).get('name', 'Unknown') if isinstance(issue.get('state'),
                                                                                        dict) else str(
                        issue.get('state', 'Unknown'))
                    formatted_issues.append(f"- {issue['summary']} (ID: {issue['idReadable']}, State: {state})")
            else:
                formatted_issues.append(
                    f"No issues found for project '{project_id}'. Note: Use project shortName, not ID.")

            self.session.add_context('assistant', {
                'name': f'issues_in_{project_id}',
                'content': '\n'.join(formatted_issues)
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to get issues for project "{project_id}": {e}. Make sure to use project shortName, not numerical ID.'
            })

    def _get_issue_details(self, args, content):
        """Retrieves detailed information for a single issue."""
        issue_id = args.get('issue_id')
        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to get issue details.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}'
        params = {
            'fields': 'idReadable,summary,description,reporter,created,updated,state,customFields'
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            issue = response.json()

            # Format the issue details for display
            details = [
                f"ID: {issue.get('idReadable')}",
                f"Summary: {issue.get('summary')}",
                f"Description: {issue.get('description', 'No description')}"
            ]

            # Handle reporter safely
            reporter = issue.get('reporter')
            if reporter:
                reporter_name = reporter.get('login') or reporter.get('name', 'Unknown')
                details.append(f"Reporter: {reporter_name}")

            # Handle dates
            if issue.get('created'):
                details.append(f"Created: {issue.get('created')}")
            if issue.get('updated'):
                details.append(f"Updated: {issue.get('updated')}")

            # Handle state
            state = issue.get('state')
            if state:
                state_name = state.get('name') if isinstance(state, dict) else str(state)
                details.append(f"State: {state_name}")

            # Add custom fields safely
            custom_fields = issue.get('customFields', [])
            if custom_fields:
                details.append("Custom Fields:")
                for field in custom_fields:
                    field_name = field.get('name')
                    field_value = field.get('value')

                    # Handle different value types
                    if field_value:
                        if isinstance(field_value, dict):
                            value_str = field_value.get('name') or field_value.get('value', str(field_value))
                        elif isinstance(field_value, list):
                            value_str = ', '.join(
                                [str(v.get('name', v)) if isinstance(v, dict) else str(v) for v in field_value])
                        else:
                            value_str = str(field_value)

                        details.append(f"  {field_name}: {value_str}")

            self.session.add_context('assistant', {
                'name': f'issue_details_{issue_id}',
                'content': '\n'.join(details)
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to get issue details: {e}'
            })

    def _create_issue(self, args, content):
        """Creates a new issue in a project."""
        project_id = args.get('project_id')  # This should be the project shortName
        summary = args.get('summary')

        if not project_id or not summary:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Project ID (shortName) and summary are required to create an issue.'
            })
            return

        url = f'{self.base_url}/api/issues'
        data = {
            'project': {'shortName': project_id},
            'summary': summary
        }

        # Add description if provided
        if content:
            data['description'] = content

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            issue = response.json()

            # YouTrack create response may not include idReadable, so handle gracefully
            issue_id = issue.get('idReadable') or issue.get('id', 'Unknown ID')

            self.session.add_context('assistant', {
                'name': 'youtrack_issue_created',
                'content': f"Successfully created issue: {issue_id}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to create issue: {e}'
            })

    def _update_issue(self, args, content):
        """Updates an existing issue."""
        issue_id = args.get('issue_id')
        summary = args.get('summary')
        description = content if content else args.get('description')

        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to update an issue.'
            })
            return

        # Build update data
        data = {}
        if summary:
            data['summary'] = summary
        if description:
            data['description'] = description

        if not data:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Nothing to update. Please provide a summary and/or description.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}'

        try:
            # Use POST for YouTrack (it uses POST for updates, not PATCH)
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_issue_updated',
                'content': f"Successfully updated issue: {issue_id}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to update issue: {e}'
            })

    def _assign_issue(self, args, content):
        """Assigns an issue to a user."""
        issue_id = args.get('issue_id')
        assignee = args.get('assignee')

        if not issue_id or not assignee:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and assignee are required.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}'
        data = {
            'customFields': [
                {
                    'name': self.assignee_field_name,
                    'value': {'login': assignee}
                }
            ]
        }

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_issue_assigned',
                'content': f"Successfully assigned issue {issue_id} to {assignee}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to assign issue: {e}'
            })

    def _update_state(self, args, content):
        """Updates the state/status of an issue."""
        issue_id = args.get('issue_id')
        state = args.get('state')

        if not issue_id or not state:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and state are required.'
            })
            return

        # For state fields, use direct state property instead of customFields
        url = f'{self.base_url}/api/issues/{issue_id}'
        data = {
            'state': {'name': state}
        }

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_state_updated',
                'content': f"Successfully updated issue {issue_id} state to {state}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to update state: {e}. Check that "{state}" is a valid state and workflow allows this transition.'
            })

    def _update_priority(self, args, content):
        """Updates the priority of an issue."""
        issue_id = args.get('issue_id')
        priority = args.get('priority')

        if not issue_id or not priority:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and priority are required.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}'
        data = {
            'customFields': [
                {
                    'name': self.priority_field_name,
                    'value': {'name': priority}
                }
            ]
        }

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_priority_updated',
                'content': f"Successfully updated issue {issue_id} priority to {priority}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to update priority: {e}'
            })

    def _update_type(self, args, content):
        """Updates the type of an issue."""
        issue_id = args.get('issue_id')
        issue_type = args.get('type')

        if not issue_id or not issue_type:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and type are required.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}'
        data = {
            'customFields': [
                {
                    'name': self.type_field_name,
                    'value': {'name': issue_type}
                }
            ]
        }

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_type_updated',
                'content': f"Successfully updated issue {issue_id} type to {issue_type}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to update type: {e}'
            })

    def _add_comment(self, args, content):
        """Adds a comment to an issue."""
        issue_id = args.get('issue_id')

        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to add a comment.'
            })
            return

        if not content:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Comment content is required.'
            })
            return

        url = f'{self.base_url}/api/issues/{issue_id}/comments'
        data = {
            'text': content
        }

        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()

            self.session.add_context('assistant', {
                'name': 'youtrack_comment_added',
                'content': f"Successfully added comment to issue {issue_id}"
            })

        except requests.exceptions.RequestException as e:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': f'Failed to add comment: {e}'
            })
