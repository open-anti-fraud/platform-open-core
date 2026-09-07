from uuid import UUID
from typing import Optional

from django.conf import settings
from django.db import models
from platform_lib.exceptions import InvalidToken, EmptyToken


class Token:
    valid_types = ['access', 'agent', 'service', 'task']

    def __init__(self, _type: str, _id: str, model: Optional[models.Model] = None):
        self.type = _type.lower()
        self.id = _id
        self.model = model

    def __str__(self):
        return self.id

    def __eq__(self, other):
        return self.type == other.type and self.id == other.id

    def is_access(self):
        return self.type == 'access'

    def is_agent(self):
        return self.type == 'agent'

    def is_service(self):
        return self.type == 'service'

    @property
    def workspace_id(self):
        if self.is_access() or self.is_agent():
            return self.model.workspace_id
        elif self.is_service():
            return None
        else:
            raise InvalidToken()

    @classmethod
    def from_string(cls, uuid: str):
        from user_domain.models import Access
        from collector_domain.models import Agent

        if uuid == '':
            raise EmptyToken()

        if uuid == settings.SERVICE_KEY:
            return Token('service', uuid)

        if uuid is None:
            raise InvalidToken()

        try:
            UUID(uuid, version=4)
        except ValueError:
            raise InvalidToken()

        models = [
            ('access', Access),
            ('agent', Agent),
        ]

        for name, model in models:
            try:
                obj = model.objects.get(id=uuid)
                return Token(name, uuid, obj)
            except model.DoesNotExist:
                continue

        raise InvalidToken()
