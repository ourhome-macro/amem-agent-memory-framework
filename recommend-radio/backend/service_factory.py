"""Composition root shared by HTTP requests and workers, with explicit ownership."""

from __future__ import annotations

from contextlib import ExitStack
from functools import cached_property

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID


class MusicServices:
    def __init__(
        self, *, user_id=LEGACY_OWNER_USER_ID, db_path=None, bili_client=None, amem_runtime=None
    ):
        self.user_id = user_id
        self.db_path = db_path or DEFAULT_DB_PATH
        self._borrowed_client = bili_client
        self._borrowed_runtime = amem_runtime
        self._resources = ExitStack()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._resources.__exit__(*exc)

    def close(self):
        self._resources.close()

    @cached_property
    def client(self):
        if self._borrowed_client is not None:
            return self._borrowed_client
        from auth_service import AuthService
        from bili_client import BiliClient

        auth = AuthService(db_path=self.db_path, user_id=self.user_id)
        self._resources.callback(auth.session.close)
        client = BiliClient(cookie_provider=auth.get_cookie_header)
        self._resources.callback(client.close)
        return client

    @cached_property
    def memory(self):
        if self._borrowed_runtime is not None:
            return self._borrowed_runtime
        from amem_runtime import build_amem_runtime

        bridge, projector = build_amem_runtime()
        # ExitStack drains the projector before it closes the underlying channel.
        self._resources.callback(getattr(bridge, "close", lambda: None))
        self._resources.callback(getattr(projector, "close", lambda: None))
        return bridge, projector

    @cached_property
    def recommendations(self):
        from recommendation_service import RecommendationService

        bridge, projector = self.memory
        return RecommendationService(
            db_path=self.db_path,
            user_id=self.user_id,
            bili_client=self.client,
            amem_bridge=bridge,
            profile_projector=projector,
        )

    @cached_property
    def dialogue(self):
        from dialogue_service import MusicDialogueService

        return MusicDialogueService(
            db_path=self.db_path, user_id=self.user_id, recommendation_service=self.recommendations
        )

    @cached_property
    def discovery(self):
        from discovery_service import DiscoveryService

        return DiscoveryService(str(self.db_path), user_id=self.user_id, bili_client=self.client)
