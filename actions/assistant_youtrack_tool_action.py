import requests
from session_handler import InteractionAction

# Centralized configuration and endpoints
DEFAULT_CONFIG = {
    'state_field_name': 'Status',
    'priority_field_name': 'Priority',
    'type_field_name': 'Type',
    'assignee_field_name': 'Assignee',
    'default_state_filter': 'status:Open',
    'default_get_issues_query': 'project:{project_short_name} {query_filter}'
}

ENDPOINTS = {
    'get_projects': 'admin/projects',
    'get_issues': 'issues',
    'get_issue_details': 'issues/{issue_id}',
    'create_issue': 'issues',
    'update_issue': 'issues/{issue_id}',
    'assign_issue': 'issues/{issue_id}',
    'update_state': 'issues/{issue_id}',
    'update_priority': 'issues/{issue_id}',
    'update_type': 'issues/{issue_id}',
    'add_comment': 'issues/{issue_id}/comments'
}


class AssistantYoutrackToolAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.base_url = self.session.conf.get_option('YOUTRACK', 'base_url')
        self.api_key = self.session.conf.get_option('YOUTRACK', 'api_key')

        # Merge the default configuration with user-defined settings from config.ini
        self.config = DEFAULT_CONFIG.copy()
        self.config['state_field_name'] = self.session.conf.get_option('YOUTRACK', 'state_field_name', fallback=self.config['state_field_name'])
        self.config['priority_field_name'] = self.session.conf.get_option('YOUTRACK', 'priority_field_name', fallback=self.config['priority_field_name'])
        self.config['type_field_name'] = self.session.conf.get_option('YOUTRACK', 'type_field_name', fallback=self.config['type_field_name'])
        self.config['assignee_field_name'] = self.session.conf.get_option('YOUTRACK', 'assignee_field_name', fallback=self.config['assignee_field_name'])
        self.config['default_state_filter'] = self.session.conf.get_option('YOUTRACK', 'default_state_filter', fallback=self.config['default_state_filter'])

        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _construct_url(self, endpoint, **kwargs):
        """Constructs the full URL for the API request."""
        return f'{self.base_url}/api/{endpoint.format(**kwargs)}'

    def _make_request(self, method, url, params=None, data=None):
        """Makes an HTTP request to the YouTrack API."""
        try:
            response = requests.request(method, url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_message = f'Failed to {method} {url}: {e}. Params: {params}, Data: {data}'
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': error_message
            })
            return None

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
        url = self._construct_url(ENDPOINTS['get_projects'])
        params = {'fields': 'id,name,shortName'}
        response = self._make_request('GET', url, params=params)
        if response is None:
            return  # Error already handled in _make_request

        formatted_projects = [
            f"- {project['name']} (ID: {project['shortName']})"
            for project in response
        ]
        self.session.add_context('assistant', {
            'name': 'youtrack_projects',
            'content': '\n'.join(formatted_projects)
        })

    def _get_issues(self, args, content):
        """Retrieves issues for a specific project using its short name."""
        project_short_name = args.get('project_id')
        if not project_short_name:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Project short name is required to get issues.'
            })
            return

        query_filter = args.get('query', self.config['default_state_filter'])
        full_query = self.config['default_get_issues_query'].format(project_short_name=project_short_name, query_filter=query_filter)

        url = self._construct_url(ENDPOINTS['get_issues'])
        params = {
            'query': full_query,
            'fields': 'idReadable,summary,state'
        }
        response = self._make_request('GET', url, params=params)
        if response is None:
            return  # Error already handled in _make_request

        formatted_issues = []
        if response:
            for issue in response:
                state = issue.get('state', {}).get('name', 'Unknown') if isinstance(issue.get('state'), dict) else str(issue.get('state', 'Unknown'))
                formatted_issues.append(f"- {issue['summary']} (ID: {issue['idReadable']}, State: {state})")
        else:
            formatted_issues.append(f"No issues found for project '{project_short_name}'.")

        self.session.add_context('assistant', {
            'name': f'issues_in_{project_short_name}',
            'content': '\n'.join(formatted_issues)
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

        url = self._construct_url(ENDPOINTS['get_issue_details'], issue_id=issue_id)
        params = {
            'fields': 'idReadable,summary,description,reporter,created,updated,state,customFields'
        }
        response = self._make_request('GET', url, params=params)
        if response is None:
            return  # Error already handled in _make_request

        details = [
            f"ID: {response.get('idReadable')}",
            f"Summary: {response.get('summary')}",
            f"Description: {response.get('description', 'No description')}"
        ]

        reporter = response.get('reporter')
        if reporter:
            reporter_name = reporter.get('login') or reporter.get('name', 'Unknown')
            details.append(f"Reporter: {reporter_name}")

        if response.get('created'):
            details.append(f"Created: {response.get('created')}")
        if response.get('updated'):
            details.append(f"Updated: {response.get('updated')}")

        state = response.get('state')
        if state:
            state_name = state.get('name') if isinstance(state, dict) else str(state)
            details.append(f"State: {state_name}")

        custom_fields = response.get('customFields', [])
        if custom_fields:
            details.append("Custom Fields:")
            for field in custom_fields:
                field_name = field.get('name')
                field_value = field.get('value')

                if field_value:
                    if isinstance(field_value, dict):
                        value_str = field_value.get('name') or field_value.get('value', str(field_value))
                    elif isinstance(field_value, list):
                        value_str = ', '.join([str(v.get('name', v)) if isinstance(v, dict) else str(v) for v in field_value])
                    else:
                        value_str = str(field_value)

                    details.append(f"  {field_name}: {value_str}")

        self.session.add_context('assistant', {
            'name': f'issue_details_{issue_id}',
            'content': '\n'.join(details)
        })

    def _create_issue(self, args, content):
        """Creates a new issue in a project."""
        project_short_name = args.get('project_id')
        summary = args.get('summary')

        if not project_short_name or not summary:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Project short name and summary are required to create an issue.'
            })
            return

        url = self._construct_url(ENDPOINTS['create_issue'])
        data = {
            'project': {'shortName': project_short_name},
            'summary': summary
        }

        if content:
            data['description'] = content

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        issue_id = response.get('idReadable') or response.get('id', 'Unknown ID')
        self.session.add_context('assistant', {
            'name': 'youtrack_issue_created',
            'content': f"Successfully created issue: {issue_id}"
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

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_issue_updated',
            'content': f"Successfully updated issue: {issue_id}"
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

        url = self._construct_url(ENDPOINTS['assign_issue'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['assignee_field_name'],
                    'value': {'login': assignee}
                }
            ]
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_issue_assigned',
            'content': f"Successfully assigned issue {issue_id} to {assignee}"
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

        url = self._construct_url(ENDPOINTS['update_state'], issue_id=issue_id)
        data = {
            'state': {'name': state}
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_state_updated',
            'content': f"Successfully updated issue {issue_id} state to {state}"
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

        url = self._construct_url(ENDPOINTS['update_priority'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['priority_field_name'],
                    'value': {'name': priority}
                }
            ]
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_priority_updated',
            'content': f"Successfully updated issue {issue_id} priority to {priority}"
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

        url = self._construct_url(ENDPOINTS['update_type'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['type_field_name'],
                    'value': {'name': issue_type}
                }
            ]
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_type_updated',
            'content': f"Successfully updated issue {issue_id} type to {issue_type}"
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

        url = self._construct_url(ENDPOINTS['add_comment'], issue_id=issue_id)
        data = {
            'text': content
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_comment_added',
            'content': f"Successfully added comment to issue {issue_id}"
        })
