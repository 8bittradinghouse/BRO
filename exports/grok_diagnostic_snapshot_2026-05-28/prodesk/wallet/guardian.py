from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from ..gateway import BaseGateway
from .wallet_controller import (
    LiveWalletDoctrine,
    PaperWalletDoctrine,
    WalletDoctrine,
    WalletDoctrineBase,
    create_wallet_controller as _create_wallet_controller,
    create_wallet_doctrine as _create_wallet_doctrine,
)

WalletGuardianBase = WalletDoctrineBase
PaperWalletGuardian = PaperWalletDoctrine
LiveWalletGuardian = LiveWalletDoctrine


def create_wallet_guardian(
    cfg: Mapping[str, Any],
    *,
    mode: str,
    gateway: Optional[BaseGateway] = None,
    event_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    auth_cfg: Optional[Mapping[str, Any]] = None,
) -> WalletGuardianBase:
    """Canonical owner-surface factory for wallet guardian authority.

    Slice 1 preserves the existing doctrine/controller behavior and introduces
    a single obvious owner import surface for Packet 3.
    """

    return _create_wallet_doctrine(
        cfg,
        mode=mode,
        gateway=gateway,
        event_logger=event_logger,
        auth_cfg=auth_cfg,
    )


def create_wallet_guardian_controller(
    cfg: Mapping[str, Any],
    *,
    mode: str,
    gateway: Optional[BaseGateway] = None,
    event_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    auth_cfg: Optional[Mapping[str, Any]] = None,
) -> WalletGuardianBase:
    """Compatibility alias for controller-shaped construction."""

    return _create_wallet_controller(
        cfg,
        mode=mode,
        gateway=gateway,
        event_logger=event_logger,
        auth_cfg=auth_cfg,
    )


__all__ = [
    "LiveWalletDoctrine",
    "LiveWalletGuardian",
    "PaperWalletDoctrine",
    "PaperWalletGuardian",
    "WalletDoctrine",
    "WalletDoctrineBase",
    "WalletGuardianBase",
    "create_wallet_guardian",
    "create_wallet_guardian_controller",
]
