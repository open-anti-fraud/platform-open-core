from contextvars import ContextVar
from typing import Any


class ContextManager:
    def set_context_var(self, var_name: str, var_value: Any) -> None:
        var = ContextVar(var_name)
        var.set(var_value)
        setattr(self, var_name, var)

    def get_context_var(self, name: str):
        return getattr(self, name)

    def get_context_var_value(self, name: str):
        return self.get_context_var(name).get()

    def delete_context_var(self, name: str):
        delattr(self, name)


context_manager = ContextManager()
