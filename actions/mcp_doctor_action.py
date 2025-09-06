from __future__ import annotations

from base_classes import InteractionAction
from mcp.client import get_or_create_client


class McpDoctorAction(InteractionAction):
    """Report MCP status: SDK detection, servers, transports, and effective settings."""

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        client = get_or_create_client(self.session)
        lines = ["MCP Doctor:"]
        # Config
        try:
            use_sdk = bool(self.session.get_option('MCP', 'use_sdk', fallback=False))
        except Exception:
            use_sdk = False
        lines.append(f"- use_sdk: {use_sdk}")
        # SDK detection
        sdk_enabled = False
        try:
            sdk_enabled = getattr(client, 'use_sdk', False) and client._sdk_enabled()
        except Exception:
            sdk_enabled = False
        lines.append(f"- sdk_available: {sdk_enabled}")

        # Servers
        servers = client.list_servers() or []
        if not servers:
            lines.append("- servers: (none)")
        else:
            lines.append("- servers:")
            for s in servers:
                t = f"{s.transport}:{s.url or s.cmd}"
                via = 'sdk' if (sdk_enabled and s.sdk is not None) else 'http' if s.transport == 'http' else 'stdio'
                lines.append(f"  • {s.name}  [{t}]  via={via}")
        msg = "\n".join(lines)
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit('status', {'message': msg})
            else:
                self.session.utils.output.write(msg)
        except Exception:
            print(msg)

