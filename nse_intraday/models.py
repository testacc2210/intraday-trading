"""Domain models and JSON-serializable state structures for NSE intraday trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Side(str, Enum):
    """Order / position side."""

    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    """Daily execution state machine status."""

    IDLE = "IDLE"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    KILL_SWITCH = "KILL_SWITCH"


class ExitReason(str, Enum):
    """Reason a position was closed."""

    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"
    TIME_SQUARE_OFF_1510 = "TIME_SQUARE_OFF_1510"
    KILL_SWITCH = "KILL_SWITCH"
    VWAP_REVERSAL = "VWAP_REVERSAL"


@dataclass(frozen=True)
class Candle:
    """Standard time-based OHLCV candle bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def typical_price(self) -> float:
        """Typical price (H + L + C) / 3 used in VWAP calculations."""
        return (self.high + self.low + self.close) / 3.0

    @property
    def body_size(self) -> float:
        """Absolute candle body length."""
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        """High-to-low candle range."""
        return max(self.high - self.low, 0.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize candle to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candle:
        """Deserialize candle from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=int(data["volume"]),
        )


@dataclass(frozen=True)
class Signal:
    """Validated intraday trade setup signal."""

    symbol: str
    side: Side
    entry_price: float
    stop_loss: float
    target: float
    risk_reward: float
    conviction_score: float
    vwap: float
    rvol: float
    orh: float
    orl: float
    reason: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize signal to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "risk_reward": self.risk_reward,
            "conviction_score": self.conviction_score,
            "vwap": self.vwap,
            "rvol": self.rvol,
            "orh": self.orh,
            "orl": self.orl,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class CostBreakdown:
    """Itemized NSE Intraday (MIS) slippage, brokerage, and statutory taxes."""

    gross_pnl: float
    entry_slippage: float
    exit_slippage: float
    brokerage: float
    stt: float
    exchange_txn_charge: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float
    net_pnl: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize cost breakdown to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostBreakdown:
        """Deserialize cost breakdown from dictionary."""
        return cls(
            gross_pnl=float(data["gross_pnl"]),
            entry_slippage=float(data["entry_slippage"]),
            exit_slippage=float(data["exit_slippage"]),
            brokerage=float(data["brokerage"]),
            stt=float(data["stt"]),
            exchange_txn_charge=float(data["exchange_txn_charge"]),
            sebi_charges=float(data["sebi_charges"]),
            stamp_duty=float(data["stamp_duty"]),
            gst=float(data["gst"]),
            total_charges=float(data["total_charges"]),
            net_pnl=float(data["net_pnl"]),
        )


@dataclass
class Position:
    """Active or closed intraday equity position."""

    trade_id: str
    symbol: str
    side: Side
    quantity: int
    requested_entry: float
    fill_entry: float
    stop_loss: float
    target: float
    risk_amount: float
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[ExitReason] = None
    costs: Optional[CostBreakdown] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize position to JSON-compatible dictionary."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "requested_entry": self.requested_entry,
            "fill_entry": self.fill_entry,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "risk_amount": self.risk_amount,
            "entry_time": self.entry_time.isoformat(),
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "costs": self.costs.to_dict() if self.costs else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        """Deserialize position from dictionary."""
        return cls(
            trade_id=str(data["trade_id"]),
            symbol=str(data["symbol"]),
            side=Side(data["side"]),
            quantity=int(data["quantity"]),
            requested_entry=float(data["requested_entry"]),
            fill_entry=float(data["fill_entry"]),
            stop_loss=float(data["stop_loss"]),
            target=float(data["target"]),
            risk_amount=float(data["risk_amount"]),
            entry_time=datetime.fromisoformat(data["entry_time"]),
            exit_price=float(data["exit_price"]) if data.get("exit_price") is not None else None,
            exit_time=(
                datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None
            ),
            exit_reason=(
                ExitReason(data["exit_reason"]) if data.get("exit_reason") else None
            ),
            costs=CostBreakdown.from_dict(data["costs"]) if data.get("costs") else None,
        )


@dataclass
class DailyState:
    """Lightweight daily state persisted between stateless cron/Lambda invocations."""

    trading_date: str
    status: TradeStatus = TradeStatus.IDLE
    trades_taken: int = 0
    active_position: Optional[Position] = None
    closed_positions: list[Position] = field(default_factory=list)
    kill_switch_reason: Optional[str] = None
    last_updated_ist: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize daily state to JSON-compatible dictionary."""
        return {
            "trading_date": self.trading_date,
            "status": self.status.value,
            "trades_taken": self.trades_taken,
            "active_position": self.active_position.to_dict() if self.active_position else None,
            "closed_positions": [pos.to_dict() for pos in self.closed_positions],
            "kill_switch_reason": self.kill_switch_reason,
            "last_updated_ist": self.last_updated_ist,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyState:
        """Deserialize daily state from dictionary."""
        return cls(
            trading_date=str(data["trading_date"]),
            status=TradeStatus(data.get("status", TradeStatus.IDLE.value)),
            trades_taken=int(data.get("trades_taken", 0)),
            active_position=(
                Position.from_dict(data["active_position"])
                if data.get("active_position")
                else None
            ),
            closed_positions=[
                Position.from_dict(item) for item in data.get("closed_positions", [])
            ],
            kill_switch_reason=data.get("kill_switch_reason"),
            last_updated_ist=str(data.get("last_updated_ist", "")),
        )
