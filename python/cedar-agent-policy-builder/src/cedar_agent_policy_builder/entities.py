from __future__ import annotations

from .types import CedarAgentConfig, EntityJson


def generate_entities(config: CedarAgentConfig) -> list[EntityJson]:
    entities: list[EntityJson] = []

    if config.roles:
        for role_name in config.roles:
            entities.append(EntityJson(uid={"type": "Role", "id": role_name}, attrs={}, parents=[]))

    resource = config.resource if config.resource else {"type": "McpServer", "id": "default"}
    entities.append(EntityJson(uid=resource, attrs={}, parents=[]))

    return entities
