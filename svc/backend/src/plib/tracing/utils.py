from plib.tracing import _NoOpSpan, _NoOpTracer, _TRACING_ENABLED


class ContextStub(_NoOpSpan):
    """No-op context manager used when tracing is disabled."""


def get_tracer(name=None):
    if not _TRACING_ENABLED:
        return None
    return _NoOpTracer()


def get_current_tracing_context():
    return {}
