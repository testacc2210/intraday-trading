"""Standalone Native Android Mobile App for NSE Best Intraday Algorithm."""

import threading
from datetime import datetime, time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from nse_intraday.config import TradingConfig
from nse_intraday.data_provider import SyntheticReplayProvider
from nse_intraday.engine import IntradayEngine
from nse_intraday.state_store import LocalJSONStateStore
from nse_intraday.utils import is_market_day, is_within_entry_window, now_ist


class StyledCard(BoxLayout):
    """Custom rounded dark card."""

    def __init__(self, bg_color=(0.08, 0.11, 0.18, 1), **kwargs):
        super().__init__(**kwargs)
        self.bg_color = bg_color
        with self.canvas.before:
            Color(*self.bg_color)
            self.rect = RoundedRectangle(size=self.size, pos=self.pos, radius=[16])
        self.bind(size=self._update_rect, pos=self._update_rect)

    def _update_rect(self, *args):
        self.rect.size = self.size
        self.rect.pos = self.pos


class BestIntradayApp(App):
    """Main Kivy Mobile App running 100% on Android device."""

    def build(self):
        self.title = "Best Intraday - NSE Scanner"

        # Root layout
        root = BoxLayout(orientation="vertical", padding=14, spacing=12)

        # Header bar
        header = StyledCard(
            orientation="horizontal",
            size_hint=(1, None),
            height=60,
            padding=[16, 10],
            bg_color=(0.06, 0.09, 0.15, 1),
        )
        title_lbl = Label(
            text="⚡ [b]Best Intraday[/b] (NSE)",
            markup=True,
            font_size="20sp",
            color=(0.22, 0.74, 0.97, 1),
            halign="left",
            valign="middle",
        )
        title_lbl.bind(size=title_lbl.setter("text_size"))
        header.add_widget(title_lbl)
        root.add_widget(header)

        # Capital Input Box
        cap_card = StyledCard(
            orientation="horizontal",
            size_hint=(1, None),
            height=54,
            padding=[16, 8],
            spacing=10,
            bg_color=(0.09, 0.13, 0.21, 1),
        )
        cap_lbl = Label(
            text="Capital (₹):",
            font_size="15sp",
            size_hint=(0.4, 1),
            color=(0.6, 0.7, 0.8, 1),
        )
        self.cap_input = TextInput(
            text="50000",
            multiline=False,
            input_filter="float",
            size_hint=(0.6, 1),
            background_color=(0.12, 0.17, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            font_size="16sp",
        )
        cap_card.add_widget(cap_lbl)
        cap_card.add_widget(self.cap_input)
        root.add_widget(cap_card)

        # Action Buttons Layout
        btn_box = BoxLayout(orientation="horizontal", size_hint=(1, None), height=56, spacing=8)

        self.btn_scan = Button(
            text="⚡ Scan Live Market",
            font_size="16sp",
            bold=True,
            background_normal="",
            background_color=(0.01, 0.52, 0.78, 1),
        )
        self.btn_scan.bind(on_press=lambda x: self.start_scan(mode="live"))

        self.btn_sim = Button(
            text="🧪 Test Replay",
            font_size="15sp",
            bold=True,
            size_hint=(0.4, 1),
            background_normal="",
            background_color=(0.2, 0.28, 0.4, 1),
        )
        self.btn_sim.bind(on_press=lambda x: self.start_scan(mode="sim"))

        btn_box.add_widget(self.btn_scan)
        btn_box.add_widget(self.btn_sim)
        root.add_widget(btn_box)

        # Result ScrollView
        scroll = ScrollView(size_hint=(1, 1))
        self.result_box = StyledCard(
            orientation="vertical",
            size_hint=(1, None),
            padding=16,
            spacing=10,
            bg_color=(0.07, 0.1, 0.16, 1),
        )
        self.result_box.bind(minimum_height=self.result_box.setter("height"))

        self.status_label = Label(
            text="Tap [b]Scan Live Market[/b] at 09:35 AM IST\nor [b]Test Replay[/b] for offline demo.",
            markup=True,
            font_size="16sp",
            color=(0.8, 0.85, 0.9, 1),
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=120,
        )
        self.status_label.bind(size=self.status_label.setter("text_size"))
        self.result_box.add_widget(self.status_label)
        scroll.add_widget(self.result_box)
        root.add_widget(scroll)

        # Copy button (hidden until trade signal appears)
        self.btn_copy = Button(
            text="📋 Copy Trade Summary",
            font_size="15sp",
            size_hint=(1, None),
            height=48,
            background_normal="",
            background_color=(0.1, 0.65, 0.45, 1),
            opacity=0,
            disabled=True,
        )
        self.btn_copy.bind(on_press=self.copy_trade_text)
        root.add_widget(self.btn_copy)

        self.last_trade_text = ""
        return root

    def start_scan(self, mode="live"):
        self.btn_scan.disabled = True
        self.btn_sim.disabled = True
        self.status_label.text = "🔍 Scanning 15 Liquid NIFTY Stocks..."
        self.btn_copy.opacity = 0
        self.btn_copy.disabled = True

        try:
            capital = float(self.cap_input.text)
        except ValueError:
            capital = 50000.0

        threading.Thread(target=self._run_scan_thread, args=(mode, capital), daemon=True).start()

    def _run_scan_thread(self, mode: str, capital: float):
        import tempfile

        now = now_ist()
        config = TradingConfig.from_env()
        config.capital = capital

        if mode == "sim":
            with tempfile.TemporaryDirectory() as tmp_dir:
                config.state_file_path = tmp_dir
                store = LocalJSONStateStore(tmp_dir)
                provider = SyntheticReplayProvider(scenario="breakout_target")
                engine = IntradayEngine(config=config, data_provider=provider, state_store=store)
                sim_time = now.replace(hour=9, minute=45, second=0, microsecond=0)
                res = engine.run_cycle(now=sim_time)
            Clock.schedule_once(lambda dt: self._update_ui(res, capital), 0)
            return

        # Live Scan
        is_mkt_day = is_market_day(now, config.nse_holidays_2026)
        if not is_mkt_day:
            res = {"status": "NO_ACTION", "reason": "MARKET_HOLIDAY_OR_WEEKEND"}
        elif now.time() >= config.square_off_time:
            res = {"status": "NO_ACTION", "reason": "PAST_SQUARE_OFF_TIME"}
        elif not is_within_entry_window(now.time(), config):
            res = {"status": "NO_ACTION", "reason": "OUTSIDE_ENTRY_WINDOW"}
        else:
            engine = IntradayEngine(config=config)
            res = engine.run_cycle(now=now)

        Clock.schedule_once(lambda dt: self._update_ui(res, capital), 0)

    def _update_ui(self, res: dict, capital: float):
        self.btn_scan.disabled = False
        self.btn_sim.disabled = False

        status = res.get("status")
        if status in {"ENTERED", "HOLDING", "SIGNAL_TRIGGERED"}:
            sym = res.get("symbol", "N/A")
            side = res.get("side", "BUY")
            entry = res.get("requested_entry") or res.get("fill_entry") or res.get("last_price", 0.0)
            sl = res.get("stop_loss", 0.0)
            tgt = res.get("target", 0.0)
            qty = res.get("quantity") or int((capital * 0.01) / max(0.05, abs(entry - sl)))
            risk = round(abs(entry - sl) * qty, 2)
            gain = round(abs(tgt - entry) * qty, 2)

            side_color = "[color=10b981]" if side == "BUY" else "[color=ef4444]"
            text = (
                f"[size=22sp][b]{sym}[/b][/size]  {side_color}[b]({side})[/b][/color]\n"
                f"[color=94a3b8]Setup: 15m ORB + Session VWAP[/color]\n\n"
                f"[b]Entry:[/b] ₹{entry:.2f}\n"
                f"[b]Stop-Loss:[/b] ₹{sl:.2f}\n"
                f"[b]Target (1:2):[/b] ₹{tgt:.2f}\n"
                f"[b]Quantity:[/b] {qty} shares\n"
                f"[b]Max Risk (1%):[/b] ₹{risk:.2f} | [b]Target Gain:[/b] ₹{gain:.2f}"
            )
            self.last_trade_text = (
                f"NSE INTRADAY: {side} {sym} @ ₹{entry:.2f} | SL: ₹{sl:.2f} | "
                f"Target: ₹{tgt:.2f} | Qty: {qty} shares"
            )
            self.status_label.text = text
            self.status_label.height = 200
            self.btn_copy.opacity = 1
            self.btn_copy.disabled = False
        else:
            reason = res.get("reason", "NO_SIGNAL")
            if reason == "PAST_SQUARE_OFF_TIME":
                msg = (
                    "⏳ [b]Market Closed[/b] (Past 15:10 IST)\n\n"
                    "Live scanning operates between 09:30-10:30 & 13:30-14:15 IST.\n\n"
                    "Tap [b]Test Replay[/b] to preview a breakout signal!"
                )
            elif reason == "OUTSIDE_ENTRY_WINDOW":
                msg = (
                    "⏱️ [b]Outside Entry Window[/b]\n\n"
                    "Active windows tomorrow: 09:30-10:30 & 13:30-14:15 IST."
                )
            else:
                msg = (
                    "📊 [b]No Breakout Setup Triggered[/b]\n\n"
                    "All 15 stocks currently consolidating within their 15m Opening Range."
                )
            self.status_label.text = msg
            self.status_label.height = 140

    def copy_trade_text(self, *args):
        if self.last_trade_text:
            Clipboard.copy(self.last_trade_text)
            self.btn_copy.text = "✅ Copied to Clipboard!"
            Clock.schedule_once(
                lambda dt: setattr(self.btn_copy, "text", "📋 Copy Trade Summary"), 2.5
            )


if __name__ == "__main__":
    BestIntradayApp().run()
