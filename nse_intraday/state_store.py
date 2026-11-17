"""Lightweight stateless persistence backends (Local JSON & AWS S3) for daily state."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from nse_intraday.models import DailyState


class StateStore(ABC):
    """Abstract state repository enabling stateless cron / AWS Lambda invocations."""

    @abstractmethod
    def load_state(self, trading_date: str) -> DailyState:
        """Load state for `trading_date` (YYYY-MM-DD), returning fresh IDLE state if absent."""

    @abstractmethod
    def save_state(self, state: DailyState) -> None:
        """Persist `DailyState` atomically."""


class LocalJSONStateStore(StateStore):
    """Atomic local JSON file store for CLI / cron execution."""

    def __init__(self, base_path: str | Path = "state") -> None:
        self.base_path = Path(base_path)

    def _resolve_path(self, trading_date: str) -> Path:
        if self.base_path.suffix.lower() == ".json":
            return self.base_path
        return self.base_path / f"{trading_date}.json"

    def load_state(self, trading_date: str) -> DailyState:
        path = self._resolve_path(trading_date)
        if not path.exists():
            return DailyState(trading_date=trading_date)

        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        state = DailyState.from_dict(data)
        # If sharing a single JSON file across dates, roll over cleanly on new date
        if state.trading_date != trading_date:
            return DailyState(trading_date=trading_date)
        return state

    def save_state(self, state: DailyState) -> None:
        path = self._resolve_path(state.trading_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)


class S3StateStore(StateStore):
    """Amazon S3 state store for stateless AWS Lambda (`ap-south-1`) execution."""

    def __init__(self, bucket: str, prefix: str = "state", s3_client=None) -> None:
        if not bucket:
            raise ValueError("S3StateStore requires a non-empty S3 bucket name")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if s3_client is not None:
            self._s3 = s3_client
        else:
            import boto3  # Lazy import so local environments do not require boto3

            self._s3 = boto3.client("s3")

    def _object_key(self, trading_date: str) -> str:
        return f"{self.prefix}/{trading_date}.json" if self.prefix else f"{trading_date}.json"

    def load_state(self, trading_date: str) -> DailyState:
        key = self._object_key(trading_date)
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=key)
            raw = response["Body"].read().decode("utf-8")
            return DailyState.from_dict(json.loads(raw))
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code in {"NoSuchKey", "404", "NotFound"} or "NoSuchKey" in str(exc):
                return DailyState(trading_date=trading_date)
            raise

    def save_state(self, state: DailyState) -> None:
        key = self._object_key(state.trading_date)
        body = json.dumps(state.to_dict(), indent=2).encode("utf-8")
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
