"""Realistic NSE Intraday (MIS) slippage, brokerage, and Indian statutory cost model."""

from __future__ import annotations

from typing import Optional

from nse_intraday.models import CostBreakdown, Side
from nse_intraday.utils import round_to_nse_tick

# NSE Intraday Equity (MIS) fee constants
BROKERAGE_PCT = 0.0003          # 0.03% per executed order
BROKERAGE_CAP_INR = 20.0        # ₹20 cap per order (discount broker standard)
STT_SELL_PCT = 0.00025          # 0.025% on sell-side turnover for intraday equity
NSE_EXCHANGE_TXN_PCT = 0.0000297  # 0.00297% NSE CM transaction charge
SEBI_TURNOVER_PCT = 0.000001    # ₹10 per crore (0.0001%)
STAMP_DUTY_BUY_PCT = 0.00003    # 0.003% on buy-side turnover
GST_RATE = 0.18                 # 18% GST on (Brokerage + Exchange Txn + SEBI Charges)


def apply_slippage(
    price: float,
    side: Side,
    is_entry: bool,
    slippage_bps: float = 5.0,
    tick_size: float = 0.05,
) -> float:
    """Apply adverse execution slippage and round to NSE ₹0.05 tick size.

    Buying (Long entry or Short exit) pays higher; selling (Short entry or Long exit) receives lower.
    """
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")
    if slippage_bps < 0:
        raise ValueError(f"slippage_bps cannot be negative, got {slippage_bps}")

    is_buy_leg = (side == Side.BUY and is_entry) or (side == Side.SELL and not is_entry)
    factor = 1.0 + (slippage_bps / 10000.0) if is_buy_leg else 1.0 - (slippage_bps / 10000.0)
    return round_to_nse_tick(price * factor, tick_size=tick_size)


def calculate_nse_intraday_costs(
    side: Side,
    quantity: int,
    entry_fill: float,
    exit_fill: float,
    requested_entry: Optional[float] = None,
    requested_exit: Optional[float] = None,
) -> CostBreakdown:
    """Calculate realized Gross P&L, slippage metrics, NSE statutory taxes, and Net P&L."""
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    entry_turnover = entry_fill * quantity
    exit_turnover = exit_fill * quantity
    total_turnover = entry_turnover + exit_turnover

    if side == Side.BUY:
        buy_turnover = entry_turnover
        sell_turnover = exit_turnover
        gross_pnl = (exit_fill - entry_fill) * quantity
        entry_slip = (
            max(0.0, (entry_fill - requested_entry) * quantity)
            if requested_entry is not None
            else 0.0
        )
        exit_slip = (
            max(0.0, (requested_exit - exit_fill) * quantity)
            if requested_exit is not None
            else 0.0
        )
    else:
        buy_turnover = exit_turnover
        sell_turnover = entry_turnover
        gross_pnl = (entry_fill - exit_fill) * quantity
        entry_slip = (
            max(0.0, (requested_entry - entry_fill) * quantity)
            if requested_entry is not None
            else 0.0
        )
        exit_slip = (
            max(0.0, (exit_fill - requested_exit) * quantity)
            if requested_exit is not None
            else 0.0
        )

    entry_brokerage = min(BROKERAGE_CAP_INR, BROKERAGE_PCT * entry_turnover)
    exit_brokerage = min(BROKERAGE_CAP_INR, BROKERAGE_PCT * exit_turnover)
    brokerage = round(entry_brokerage + exit_brokerage, 4)

    stt = round(STT_SELL_PCT * sell_turnover, 4)
    exchange_txn_charge = round(NSE_EXCHANGE_TXN_PCT * total_turnover, 4)
    sebi_charges = round(SEBI_TURNOVER_PCT * total_turnover, 4)
    stamp_duty = round(STAMP_DUTY_BUY_PCT * buy_turnover, 4)
    gst = round(GST_RATE * (brokerage + exchange_txn_charge + sebi_charges), 4)

    total_charges = round(
        brokerage + stt + exchange_txn_charge + sebi_charges + stamp_duty + gst, 4
    )
    gross_pnl_rounded = round(gross_pnl, 4)
    net_pnl = round(gross_pnl_rounded - total_charges, 4)

    return CostBreakdown(
        gross_pnl=gross_pnl_rounded,
        entry_slippage=round(entry_slip, 4),
        exit_slippage=round(exit_slip, 4),
        brokerage=brokerage,
        stt=stt,
        exchange_txn_charge=exchange_txn_charge,
        sebi_charges=sebi_charges,
        stamp_duty=stamp_duty,
        gst=gst,
        total_charges=total_charges,
        net_pnl=net_pnl,
    )
