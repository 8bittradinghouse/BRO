from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional


def clamp_binary_price(price: float) -> float:
    return max(0.0, min(1.0, float(price)))


def binary_buy_capital_usd(*, price: float, size_shares: float) -> float:
    return max(0.0, clamp_binary_price(price) * max(0.0, float(size_shares)))


def binary_sell_gross_liability_usd(*, size_shares: float) -> float:
    # Short binary exposure is collateralized against the full $1/share
    # settlement obligation. Sale proceeds are already reflected in cash.
    return max(0.0, float(size_shares))


def binary_order_capital_usd(*, side: str, price: float, size_shares: float) -> float:
    normalized_side = str(side or "").strip().upper()
    if normalized_side == "SELL":
        return binary_sell_gross_liability_usd(size_shares=size_shares)
    return binary_buy_capital_usd(price=price, size_shares=size_shares)


def binary_position_capital_usd(*, net_shares: float, reference_price: float) -> float:
    shares = float(net_shares)
    if shares < 0.0:
        return binary_sell_gross_liability_usd(size_shares=abs(shares))
    return binary_buy_capital_usd(price=reference_price, size_shares=max(0.0, shares))


def binary_short_position_liability_usd(*, net_shares: float) -> float:
    shares = float(net_shares)
    return binary_sell_gross_liability_usd(size_shares=abs(shares)) if shares < 0.0 else 0.0


@dataclass(frozen=True)
class FillEconomics:
    taker_fee_usd: float = 0.0
    maker_rebate_usd: float = 0.0
    slippage_cost_usd: float = 0.0
    adverse_selection_cost_usd: float = 0.0
    reference_midpoint: Optional[float] = None
    fee_authority_source: str = "unknown"
    fee_category: Optional[str] = None
    fees_enabled: Optional[bool] = None
    fee_authoritative: bool = False
    taker_fee_curve_rate: Optional[float] = None

    @property
    def net_cash_adjustment_usd(self) -> float:
        return canonical_net_cash_adjustment_usd(
            maker_rebate_usd=self.maker_rebate_usd,
            taker_fee_usd=self.taker_fee_usd,
        )


@dataclass(frozen=True)
class FeeAuthority:
    fee_authority_source: str = "unknown"
    fee_category: Optional[str] = None
    fees_enabled: Optional[bool] = None
    authoritative: bool = False
    taker_fee_curve_rate: float = 0.0
    detail: str = ""


FEE_CATEGORY_GEOPOLITICS = "geopolitics"
FEE_CATEGORY_CRYPTO = "crypto"
FEE_CATEGORY_SPORTS = "sports"
FEE_CATEGORY_FINANCE = "finance"
FEE_CATEGORY_POLITICS = "politics"
FEE_CATEGORY_ECONOMICS = "economics"
FEE_CATEGORY_CULTURE = "culture"
FEE_CATEGORY_WEATHER = "weather"
FEE_CATEGORY_OTHER = "other"
FEE_CATEGORY_MENTIONS = "mentions"
FEE_CATEGORY_TECH = "tech"

FEE_CATEGORY_RATE_BY_NAME = {
    FEE_CATEGORY_CRYPTO: 0.07,
    FEE_CATEGORY_SPORTS: 0.03,
    FEE_CATEGORY_FINANCE: 0.04,
    FEE_CATEGORY_POLITICS: 0.04,
    FEE_CATEGORY_MENTIONS: 0.04,
    FEE_CATEGORY_TECH: 0.04,
    FEE_CATEGORY_ECONOMICS: 0.05,
    FEE_CATEGORY_CULTURE: 0.05,
    FEE_CATEGORY_WEATHER: 0.05,
    FEE_CATEGORY_OTHER: 0.05,
    FEE_CATEGORY_GEOPOLITICS: 0.0,
}

FEE_CATEGORY_ALIASES = {
    "crypto": FEE_CATEGORY_CRYPTO,
    "cryptocurrency": FEE_CATEGORY_CRYPTO,
    "crypto prices": FEE_CATEGORY_CRYPTO,
    "sports": FEE_CATEGORY_SPORTS,
    "finance": FEE_CATEGORY_FINANCE,
    "politics": FEE_CATEGORY_POLITICS,
    "economics": FEE_CATEGORY_ECONOMICS,
    "culture": FEE_CATEGORY_CULTURE,
    "weather": FEE_CATEGORY_WEATHER,
    "other": FEE_CATEGORY_OTHER,
    "general": FEE_CATEGORY_OTHER,
    "other / general": FEE_CATEGORY_OTHER,
    "mentions": FEE_CATEGORY_MENTIONS,
    "tech": FEE_CATEGORY_TECH,
    "technology": FEE_CATEGORY_TECH,
    "geopolitics": FEE_CATEGORY_GEOPOLITICS,
    "geopolitical": FEE_CATEGORY_GEOPOLITICS,
}

_METADATA_FEE_RATE_KEYS = (
    "takerFeeRate",
    "taker_fee_rate",
    "feeRate",
    "fee_rate",
)
_METADATA_FEES_ENABLED_KEYS = (
    "feesEnabled",
    "fees_enabled",
    "feeEnabled",
    "fee_enabled",
    "isFeeEnabled",
)


def round_usdc_fee(amount: float) -> float:
    fee = max(0.0, float(amount))
    if fee <= 0.0:
        return 0.0
    return float(
        Decimal(str(fee)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    )


def canonical_net_cash_adjustment_usd(*, maker_rebate_usd: float, taker_fee_usd: float) -> float:
    return float(max(0.0, float(maker_rebate_usd or 0.0)) - max(0.0, float(taker_fee_usd or 0.0)))


def _normalize_fee_category(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in FEE_CATEGORY_RATE_BY_NAME:
        return text
    return FEE_CATEGORY_ALIASES.get(text)


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if float(value) == 1.0:
            return True
        if float(value) == 0.0:
            return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _metadata_tags(metadata: Mapping[str, Any]) -> list[str]:
    raw_tags = metadata.get("tags")
    if isinstance(raw_tags, list):
        return [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    return []


def _category_from_metadata(metadata: Mapping[str, Any]) -> Optional[str]:
    explicit = _normalize_fee_category(metadata.get("feeCategory"))
    if explicit:
        return explicit
    explicit = _normalize_fee_category(metadata.get("category"))
    if explicit:
        return explicit
    for tag in _metadata_tags(metadata):
        normalized = _normalize_fee_category(tag)
        if normalized:
            return normalized
    return None


def resolve_fee_authority(
    *,
    market_metadata: Optional[Mapping[str, Any]] = None,
    fee_category_hint: Optional[str] = None,
    fees_enabled_hint: Optional[bool] = None,
    fee_category_override: Optional[str] = None,
    fees_enabled_override: Optional[bool] = None,
) -> FeeAuthority:
    metadata = dict(market_metadata or {})

    for key in _METADATA_FEE_RATE_KEYS:
        value = metadata.get(key)
        if isinstance(value, (int, float)):
            rate = max(0.0, float(value))
            if rate <= 1.0:
                fees_enabled = None
                for bool_key in _METADATA_FEES_ENABLED_KEYS:
                    parsed = _parse_bool(metadata.get(bool_key))
                    if parsed is not None:
                        fees_enabled = parsed
                        break
                return FeeAuthority(
                    fee_authority_source="market_metadata_verified",
                    fee_category=_category_from_metadata(metadata),
                    fees_enabled=fees_enabled,
                    authoritative=True,
                    taker_fee_curve_rate=rate,
                    detail=f"{key}={rate}",
                )

    category_from_tags = _category_from_metadata(metadata) or _normalize_fee_category(fee_category_hint)
    fees_enabled_from_hint = (
        _parse_bool(fees_enabled_hint)
        if fees_enabled_hint is not None
        else None
    )
    fees_enabled_from_metadata = None
    for bool_key in _METADATA_FEES_ENABLED_KEYS:
        parsed = _parse_bool(metadata.get(bool_key))
        if parsed is not None:
            fees_enabled_from_metadata = parsed
            break
    resolved_fees_enabled = (
        fees_enabled_from_metadata
        if fees_enabled_from_metadata is not None
        else fees_enabled_from_hint
    )
    if category_from_tags and resolved_fees_enabled is True:
        return FeeAuthority(
            fee_authority_source="market_tag_category",
            fee_category=category_from_tags,
            fees_enabled=True,
            authoritative=True,
            taker_fee_curve_rate=float(FEE_CATEGORY_RATE_BY_NAME.get(category_from_tags, 0.0)),
            detail="category+fees_enabled",
        )

    override_category = _normalize_fee_category(fee_category_override)
    override_fees_enabled = _parse_bool(fees_enabled_override)
    if override_category and override_fees_enabled is True:
        return FeeAuthority(
            fee_authority_source="profile_override",
            fee_category=override_category,
            fees_enabled=True,
            authoritative=False,
            taker_fee_curve_rate=float(FEE_CATEGORY_RATE_BY_NAME.get(override_category, 0.0)),
            detail="offline_testing_override",
        )

    return FeeAuthority(
        fee_authority_source="unknown",
        fee_category=(category_from_tags or override_category),
        fees_enabled=(
            resolved_fees_enabled
            if resolved_fees_enabled is not None
            else override_fees_enabled
        ),
        authoritative=False,
        taker_fee_curve_rate=0.0,
        detail="fee_authority_unresolved",
    )


def estimate_fill_economics(
    *,
    side: str,
    price: float,
    size_shares: float,
    is_taker: bool,
    reference_midpoint: Optional[float],
    fee_authority: FeeAuthority,
    taker_slippage_bps: float,
    adverse_selection_bps: float,
) -> FillEconomics:
    px = clamp_binary_price(price)
    size = max(0.0, float(size_shares))
    notional = px * size
    normalized_side = str(side or "").strip().upper()
    ref_mid = None
    if isinstance(reference_midpoint, (int, float)):
        ref_mid = clamp_binary_price(float(reference_midpoint))

    taker_fee = 0.0
    maker_rebate = 0.0
    slippage_cost = 0.0
    adverse_cost = 0.0
    fee_curve_rate = max(0.0, float(fee_authority.taker_fee_curve_rate or 0.0))

    if is_taker:
        taker_fee = round_usdc_fee(size * fee_curve_rate * px * (1.0 - px))
        slippage_cost = notional * (max(0.0, float(taker_slippage_bps)) / 10_000.0)
    else:
        # Daily maker rebates are a pooled competitive payout rather than a
        # deterministic per-fill cashflow. Fail closed in canonical cash truth
        # until we have exact payout evidence instead of inventing a rebate.
        maker_rebate = 0.0

    if ref_mid is not None:
        if normalized_side == "BUY":
            adverse_cost += max(0.0, px - ref_mid) * size
        elif normalized_side == "SELL":
            adverse_cost += max(0.0, ref_mid - px) * size
    adverse_cost += notional * (max(0.0, float(adverse_selection_bps)) / 10_000.0)

    return FillEconomics(
        taker_fee_usd=float(taker_fee),
        maker_rebate_usd=float(maker_rebate),
        slippage_cost_usd=float(slippage_cost),
        adverse_selection_cost_usd=float(adverse_cost),
        reference_midpoint=ref_mid,
        fee_authority_source=str(fee_authority.fee_authority_source or "unknown"),
        fee_category=(
            str(fee_authority.fee_category).strip().lower()
            if str(fee_authority.fee_category or "").strip()
            else None
        ),
        fees_enabled=fee_authority.fees_enabled,
        fee_authoritative=bool(fee_authority.authoritative),
        taker_fee_curve_rate=float(fee_curve_rate),
    )
