_TRACING_ENABLED = False


def set_tracing_flag(enabled: bool) -> None:
    global _TRACING_ENABLED
    _TRACING_ENABLED = bool(enabled)


class _NoOpSpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, *args, **kwargs):
        return None

    def set_status(self, *args, **kwargs):
        return None

    def record_exception(self, *args, **kwargs):
        return None


class _NoOpTracer:
    def start_as_current_span(self, *args, **kwargs):
        return _NoOpSpan()


class ConnectionManager:
    @staticmethod
    def init_connection(service_name: str, tracer_url: str):
        return _NoOpTracer()
