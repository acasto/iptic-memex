import requests
from base_classes import InteractionAction
from utils.tool_args import get_str
import pytz
from datetime import datetime

# Centralized configuration and endpoints
DEFAULT_CONFIG = {
    'state_field_name': 'Status',
    'priority_field_name': 'Priority',
    'type_field_name': 'Type',
    'assignee_field_name': 'Assignee',
    'default_state_filter': 'status:{Open} or status:{In Progress}',
    'default_project_filter': 'project:{project_short_name}',
    'timezone': 'UTC'  # Default timezone
}

ENDPOINTS = {
    'get_projects': 'admin/projects',
    'get_issues': 'issues',
    'get_issue_details': 'issues/{issue_id}',
    'create_issue': 'issues',
    'update_issue': 'issues/{issue_id}',
    'add_comment': 'issues/{issue_id}/comments'
}


class AssistantYoutrackToolAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.base_url = self.session.get_option('YOUTRACK', 'base_url')
        self.api_key = self.session.get_option('YOUTRACK', 'api_key')

        # Merge the default configuration with user-defined settings from config.ini
        self.config = DEFAULT_CONFIG.copy()
        self.config['state_field_name'] = self.session.get_option('YOUTRACK', 'state_field_name', fallback=self.config['state_field_name'])
        self.config['priority_field_name'] = self.session.get_option('YOUTRACK', 'priority_field_name', fallback=self.config['priority_field_name'])
        self.config['type_field_name'] = self.session.get_option('YOUTRACK', 'type_field_name', fallback=self.config['type_field_name'])
        self.config['assignee_field_name'] = self.session.get_option('YOUTRACK', 'assignee_field_name', fallback=self.config['assignee_field_name'])
        self.config['default_state_filter'] = self.session.get_option('YOUTRACK', 'default_state_filter', fallback=self.config['default_state_filter'])
        self.config['timezone'] = self.session.get_option('YOUTRACK', 'timezone', fallback=self.config['timezone'])

        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        return 'youtrack'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': [
                'mode', 'project_id', 'issue_id', 'block', 'summary', 'query', 'assignee', 'state', 'priority', 'type', 'desc'
            ],
            'description': (
                "Interact with YouTrack: list projects/issues, fetch details, create and update issues, or add comments. "
                "Configure base_url and api_key in settings."
            ),
            'required': ['mode'],
            'schema': {
                'properties': {
                    'mode': {"type": "string", "enum": [
                        "get_projects", "get_issues", "get_issue_details", "create_issue",
                        "update_summary", "update_description", "assign_issue", "update_state",
                        "update_priority", "update_type", "add_comment"
                    ], "description": "Operation to perform."},
                    'project_id': {"type": "string", "description": "Project short name (e.g., 'PROJ')."},
                    'issue_id': {"type": "string", "description": "Issue idReadable (e.g., 'PROJ-123')."},
                    'summary': {"type": "string", "description": "Issue summary for create/update."},
                    'query': {"type": "string", "description": "Additional query/filter terms."},
                    'assignee': {"type": "string", "description": "Assignee username/display name for assignment."},
                    'state': {"type": "string", "description": "New issue state/status."},
                    'priority': {"type": "string", "description": "New priority value."},
                    'type': {"type": "string", "description": "New type value."},
                    'block': {"type": "string", "description": "Identifier of a %BLOCK:...% to append to 'content'."},
                    'content': {"type": "string", "description": "Optional freeform content (e.g., description/comment text)."},
                    'desc': {"type": "string", "description": "Optional short description for UI/status; ignored by execution.", "default": ""},
                }
            },
            'auto_submit': True,
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

    def _convert_to_local_time(self, timestamp):
        """Converts a UTC timestamp to the local timezone."""
        if isinstance(timestamp, int):
            # Convert milliseconds to seconds
            timestamp /= 1000.0
            # Create a timezone-aware UTC datetime object
            utc_time = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        else:
            # Parse the ISO format timestamp and ensure it is in UTC
            utc_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).replace(tzinfo=pytz.UTC)
        
        # Convert to the specified local timezone
        local_timezone = pytz.timezone(self.config['timezone'])
        local_time = utc_time.astimezone(local_timezone)
        
        return local_time.strftime('%Y-%m-%d %H:%M:%S %Z')

    def run(self, args: dict, content: str = ""):
        mode = (get_str(args or {}, 'mode', '') or '').lower()

        # Accept common synonyms/short forms from models to preserve compatibility
        # with pseudo-tools phrasing (e.g., mode="projects")
        mode_aliases = {
            'projects': 'get_projects',
            'list_projects': 'get_projects',
            'get_project': 'get_projects',

            'issues': 'get_issues',
            'list_issues': 'get_issues',
            'search_issues': 'get_issues',

            'issue': 'get_issue_details',
            'details': 'get_issue_details',
            'issue_details': 'get_issue_details',

            'create': 'create_issue',
            'new_issue': 'create_issue',

            'assign': 'assign_issue',
            'assign_issue': 'assign_issue',

            'set_state': 'update_state',
            'set_status': 'update_state',
            'update_status': 'update_state',
            'state': 'update_state',

            'priority': 'update_priority',
            'set_priority': 'update_priority',

            'type': 'update_type',
            'set_type': 'update_type',

            'comment': 'add_comment',
            'add_comment': 'add_comment',
        }
        mode = mode_aliases.get(mode, mode)

        # Debugging: Log the mode and arguments
        # self.session.add_context('assistant', {
        #     'name': 'youtrack_debug',
        #     'content': f"Mode: {mode}, Arguments: {args}"
        # })

        if mode == 'get_projects':
            self._get_projects(args, content)
        elif mode == 'get_issues':
            self._get_issues(args, content)
        elif mode == 'get_issue_details':
            self._get_issue_details(args, content)
        elif mode == 'create_issue':
            self._create_issue(args, content)
        elif mode == 'update_summary':
            self._update_summary(args, content)
        elif mode == 'update_description':
            self._update_description(args, content)
        elif mode == 'assign_issue':
            self._assign_issue(args, content)
        elif mode == 'update_state':
            issue_id = args.get('issue_id')
            state = args.get('state')  # Use 'state' instead of 'new_status'
            if issue_id and state:
                self._update_issue_status(issue_id, state)
            else:
                self.session.add_context('assistant', {
                    'name': 'youtrack_tool_error',
                    'content': 'Issue ID and state are required for updating issue status.'
                })
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
        params = {'fields': 'id,name,shortName', 'archived': 'false'}
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
        """Retrieves issues, optionally filtered by project."""
        project_short_name = get_str(args or {}, 'project_id')  # Now optional
        custom_query = get_str(args or {}, 'query', '') or ''

        # Build query components
        query_parts = []

        # Add project filter if specified
        if project_short_name:
            project_filter = self.config['default_project_filter'].format(
                project_short_name=project_short_name
            )
            query_parts.append(project_filter)

        # Handle state/status filters
        state_keywords = ['state:', 'status:', 'State:', 'Status:']
        has_state_filter = any(keyword in custom_query for keyword in state_keywords)

        if custom_query and has_state_filter:
            # Custom query has state filter, use it as-is
            query_parts.append(f"({custom_query})")
        elif custom_query:
            # Custom query exists but no state filter, add both default and custom
            query_parts.append(f"({self.config['default_state_filter']})")
            query_parts.append(f"({custom_query})")
        else:
            # No custom query, use default state filter only
            query_parts.append(f"({self.config['default_state_filter']})")

        # Join all parts with 'and'
        full_query = ' and '.join(query_parts)

        # print(f"Full query constructed: {full_query}")  # Debugging output

        url = self._construct_url(ENDPOINTS['get_issues'])
        params = {
            'query': full_query,
            'fields': 'idReadable,summary,customFields(name,value(name))',
        }

        response = self._make_request('GET', url, params=params)
        if response is None:
            return

        # Update context name to reflect scope
        context_name = f'issues_in_{project_short_name}' if project_short_name else 'all_issues'

        formatted_issues = []
        if response:
            for issue in response:
                custom_fields = issue.get('customFields', [])
                status = 'Unknown'
                priority = 'Unknown'
                for field in custom_fields:
                    if field.get('name') == self.config['state_field_name']:
                        status = field.get('value', {}).get('name', 'Unknown')
                    elif field.get('name') == self.config['priority_field_name']:
                        priority = field.get('value', {}).get('name', 'Unknown')
                formatted_issues.append(
                    f"- {issue['summary']} (ID: {issue['idReadable']}, Status: {status}, Priority: {priority})")
        else:
            scope_desc = f"project '{project_short_name}'" if project_short_name else "the query"
            formatted_issues.append(f"No issues found for {scope_desc}.")

        self.session.add_context('assistant', {
            'name': context_name,  # Use the variable, not hardcoded string
            'content': '\n'.join(formatted_issues)
        })

    def _get_issue_details(self, args, content):
        """Retrieves detailed information for a single issue, including comments."""
        issue_id = get_str(args or {}, 'issue_id')
        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to get issue details.'
            })
            return

        url = self._construct_url(ENDPOINTS['get_issue_details'], issue_id=issue_id)
        params = {
            'fields': 'idReadable,summary,description,reporter,created,updated,state,customFields(name,value(name)),comments(text,author,created)'
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
            local_created_time = self._convert_to_local_time(response.get('created'))
            details.append(f"Created: {local_created_time}")
        if response.get('updated'):
            local_updated_time = self._convert_to_local_time(response.get('updated'))
            details.append(f"Updated: {local_updated_time}")

        custom_fields = response.get('customFields', [])
        for field in custom_fields:
            field_name = field.get('name')
            field_value = field.get('value', {}).get('name', 'Unknown') if field.get('value') else 'Unknown'
            if field_name in ['Type', 'Priority', 'Status']:
                details.append(f"{field_name}: {field_value}")

        comments = response.get('comments', [])
        if comments:
            details.append("Comments:")
            for comment in comments:
                author = comment.get('author', {}).get('login', 'Unknown')
                created = self._convert_to_local_time(comment.get('created', 'Unknown'))
                text = comment.get('text', 'No text')
                details.append(f"- Author: {author}, Created: {created}, Text: {text}")

        self.session.add_context('assistant', {
            'name': f'issue_details_{issue_id}',
            'content': '\n'.join(details)
        })

    def _create_issue(self, args, content):
        """Creates a new issue in a project."""
        project_short_name = get_str(args or {}, 'project_id')
        summary = get_str(args or {}, 'summary')

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

        # Add optional priority, type, and assignee fields
        priority = get_str(args or {}, 'priority')
        issue_type = get_str(args or {}, 'type')
        assignee = get_str(args or {}, 'assignee')

        custom_fields = []
        if priority:
            custom_fields.append({
                'name': self.config['priority_field_name'],
                '$type': 'SingleEnumIssueCustomField',
                'value': {
                    '$type': 'EnumBundleElement',
                    'name': priority
                }
            })
        if issue_type:
            custom_fields.append({
                'name': self.config['type_field_name'],
                '$type': 'SingleEnumIssueCustomField',
                'value': {
                    '$type': 'EnumBundleElement',
                    'name': issue_type
                }
            })
        if assignee:
            custom_fields.append({
                'name': self.config['assignee_field_name'],
                '$type': 'SingleUserIssueCustomField',
                'value': {
                    '$type': 'User',
                    'login': assignee
                }
            })

        if custom_fields:
            data['customFields'] = custom_fields

        # Create the issue
        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        # Get the initial entity ID
        entity_id = response.get('id', 'Unknown Entity ID')

        if entity_id == 'Unknown Entity ID':
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Failed to get the entity ID after creating the issue.'
            })
            return

        # Fetch the final issue details to confirm the final ID
        url = self._construct_url(ENDPOINTS['get_issue_details'], issue_id=entity_id)
        response = self._make_request('GET', url, params={'fields': 'idReadable'})

        # Get the final, human-readable ID
        final_issue_id = response.get('idReadable', 'Unknown Readable ID')

        # Return the final ID in the success message
        self.session.add_context('assistant', {
            'name': 'youtrack_issue_created',
            'content': f"Successfully created issue: {final_issue_id}"
        })

    def _update_summary(self, args, content):
        """Updates the summary (title) of an issue."""
        issue_id = get_str(args or {}, 'issue_id')
        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to update the summary.'
            })
            return

        summary = content if content else get_str(args or {}, 'summary')
        if not summary:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Summary content is required to update the summary.'
            })
            return

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            'summary': summary
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_summary_updated',
            'content': f"Successfully updated summary for issue: {issue_id}"
        })

    def _update_description(self, args, content):
        """Updates the description of an issue."""
        issue_id = get_str(args or {}, 'issue_id')
        if not issue_id:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID is required to update the description.'
            })
            return

        # Ensure content is provided
        if not content:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Description content is required to update the description.'
            })
            return

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            'description': content
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_description_updated',
            'content': f"Successfully updated description for issue: {issue_id}"
        })

    def _assign_issue(self, args, content):
        """Assigns an issue to a user."""
        issue_id = get_str(args or {}, 'issue_id')
        assignee = get_str(args or {}, 'assignee')

        if not issue_id or not assignee:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and assignee are required.'
            })
            return

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['assignee_field_name'],
                    '$type': 'SingleUserIssueCustomField',
                    'value': {
                        '$type': 'User',
                        'login': assignee
                    }
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

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
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

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['priority_field_name'],
                    '$type': 'SingleEnumIssueCustomField',
                    'value': {
                        '$type': 'EnumBundleElement',
                        'name': priority
                    }
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

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            'customFields': [
                {
                    'name': self.config['type_field_name'],
                    '$type': 'SingleEnumIssueCustomField',
                    'value': {
                        '$type': 'EnumBundleElement',
                        'name': issue_type
                    }
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
        issue_id = get_str(args or {}, 'issue_id')

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

    def _update_issue_status(self, issue_id, state):
        """Updates the status of an issue."""
        if not issue_id or not state:
            self.session.add_context('assistant', {
                'name': 'youtrack_tool_error',
                'content': 'Issue ID and state are required.'
            })
            return

        url = self._construct_url(ENDPOINTS['update_issue'], issue_id=issue_id)
        data = {
            "customFields": [
                {
                    "name": "Status",
                    "$type": "StateIssueCustomField",
                    "value": {
                        "$type": "StateBundleElement",
                        "name": state
                    }
                }
            ]
        }

        response = self._make_request('POST', url, data=data)
        if response is None:
            return  # Error already handled in _make_request

        self.session.add_context('assistant', {
            'name': 'youtrack_issue_status_updated',
            'content': f"Successfully updated issue {issue_id} status to {state}"
        })
