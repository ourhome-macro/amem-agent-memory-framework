from __future__ import annotations

import os
from typing import Any

from amem_bridge import AmemBridge
from amem_grpc_bridge import AmemGrpcBridge, GrpcProfileProjector
from profile_projector import ProfileProjector


def build_amem_runtime() -> tuple[Any, Any]:
    transport = os.getenv("AMEM_TRANSPORT", "embedded").strip().lower()
    if transport == "grpc":
        bridge = AmemGrpcBridge.from_env()
        return bridge, GrpcProfileProjector(bridge)
    bridge = AmemBridge.from_env()
    return bridge, ProfileProjector(bridge)
