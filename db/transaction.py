"""Request-scoped SQLAlchemy transaction and deferred reply helpers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context


class TransactionDeliveryError(RuntimeError):
    """The database committed, but one or more deferred replies failed."""

    def __init__(self, failures: list[BaseException]):
        self.failures = failures
        super().__init__(f"{len(failures)} deferred reply(s) could not be delivered")


class _SessionProxy:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def commit(self) -> None:
        # Individual stores predate request transactions and call commit().
        # Flush here so their returned rows have IDs while the outer plan owns
        # the actual commit/rollback boundary.
        self._session.flush()

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class TransactionSessionFactory:
    """Session-factory-shaped view over one open SQLAlchemy Session."""

    def __init__(self, session):
        self._proxy = _SessionProxy(session)
        self._failed = False

    @property
    def failed(self) -> bool:
        return self._failed

    def mark_failed(self) -> None:
        self._failed = True

    @contextmanager
    def __call__(self):
        yield self._proxy

    @contextmanager
    def begin(self):
        yield self._proxy


class TransactionClient:
    """Defer user replies until the database transaction commits."""

    def __init__(self, client):
        self._client = client
        self._messages: list[tuple[tuple, dict, object]] = []

    def __getattr__(self, name):
        if name != "send_message":
            return getattr(self._client, name)
        return self.send_message

    def send_message(self, *args, **kwargs):
        self._messages.append((args, kwargs, copy_context()))
        return None

    def flush_messages(self) -> None:
        pending = self._messages
        self._messages = []
        failures: list[BaseException] = []
        for args, kwargs, context in pending:
            try:
                context.run(self._client.send_message, *args, **kwargs)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise TransactionDeliveryError(failures)

    def discard_messages(self) -> None:
        self._messages.clear()


class PlanTransaction:
    def __init__(self, session_factory, client):
        self._session = session_factory()
        self.factory = TransactionSessionFactory(self._session)
        self.client = TransactionClient(client)
        self._finished = False

    @property
    def failed(self) -> bool:
        return self.factory.failed

    def commit(self) -> None:
        if self._finished:
            return
        self._session.commit()
        self._finished = True
        try:
            self.client.flush_messages()
        finally:
            self._session.close()

    def rollback(self) -> None:
        if self._finished:
            return
        self._session.rollback()
        self._finished = True
        self.client.discard_messages()
        self._session.close()
