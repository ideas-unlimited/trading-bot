"""Outbound trade alerts: email, Telegram and ntfy.

These are trade notifications, not a trading path.  A missing SMTP password
or an expired Telegram token must never stop the forward runner, so every
channel is best-effort and Notifier never raises.  Channels are configured
from the process environment (on the server the systemd EnvironmentFile at
/etc/tradingbot/alerts.env), never from git: an SMTP password and a bot
token are secrets.

Email uses the standard library (SMTP with STARTTLS by default); Telegram
and ntfy use a small HTTPS POST, so no extra dependency is added.  The
caller decides which fills are new; this module only formats and sends.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Callable, Mapping, Protocol, Sequence


class Channel(Protocol):
    """One delivery mechanism.  send() raises on failure."""

    name: str

    def send(self, subject: str, body: str) -> None: ...


def _single_line(value: str) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _money(value: Any) -> str:
    return f"${float(value):,.2f}"


def _signed_money(value: Any) -> str:
    number = float(value)
    sign = "+" if number >= 0 else "-"
    return f"{sign}${abs(number):,.2f}"


def _price(value: Any) -> str:
    number = float(value)
    if number >= 1_000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:,.4f}"
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _qty(value: Any) -> str:
    return f"{float(value):,.8f}".rstrip("0").rstrip(".")


def _utc(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def format_trade(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return (subject, body) for one entry or exit fill.

    event is deliberately plain data so both the forward replay and a
    future live loop can build it without importing the engine.
    """
    side = str(event.get("side") or "").strip().lower()
    is_buy = side == "buy"
    action = "BUY" if is_buy else "SELL"
    symbol = str(event.get("symbol") or "?")
    strategy = str(event.get("strategy") or "?")
    timeframe = str(event.get("timeframe") or "")
    ts = event.get("ts")
    price = event.get("price", 0.0)
    qty = event.get("qty", 0.0)
    notional = event.get("notional")
    stop = event.get("stop")
    take_profit = event.get("take_profit")
    entry = event.get("entry")
    if entry is None and is_buy:
        entry = price
    pnl = event.get("pnl")
    return_pct = event.get("return_pct")
    held_hours = event.get("held_hours")
    reason = event.get("reason")
    wallet = event.get("wallet")
    deploy_equity = event.get("deploy_equity")

    def level(value: Any) -> str:
        return _price(value) if value is not None else "?"

    subject = (
        f"{strategy}:{side}:{_price(price)}: "
        f"{level(entry)}/{level(stop)}/{level(take_profit)}"
    )

    lines = [
        "TRADE OPENED" if is_buy else "TRADE CLOSED",
        f"{action}  {symbol}" + (f"  {timeframe}" if timeframe else ""),
        f"strategy  {strategy}",
    ]
    if ts is not None:
        lines.append(f"filled    {_utc(ts)}")
    if entry is not None:
        lines.append(f"entry     {_price(entry)}")
    lines.append(f"price     {_price(price)}")
    lines.append(f"qty       {_qty(qty)}")
    if notional is not None:
        lines.append(f"notional  {_money(notional)}")
    if stop is not None:
        lines.append(f"stop      {_price(stop)}")
    if take_profit is not None:
        lines.append(f"target    {_price(take_profit)}")
    if not is_buy and pnl is not None:
        line = f"pnl       {_signed_money(pnl)}"
        if return_pct is not None:
            line += f"  ({float(return_pct):+.2%})"
        lines.append(line)
    if held_hours is not None:
        lines.append(f"held      {float(held_hours):.1f}h")
    if reason:
        lines.append(f"reason    {_single_line(str(reason))}")
    if wallet is not None:
        line = f"wallet    {_money(wallet)} now"
        if deploy_equity is not None:
            line += f"  ({_money(deploy_equity)} at deploy)"
        lines.append(line)
    lines.extend(("", "Simulated fill: no order was sent."))
    return _single_line(subject), "\n".join(lines)


class EmailChannel:
    """SMTP email.  Port 587 STARTTLS unless security says otherwise."""

    name = "email"

    def __init__(
        self,
        host: str,
        recipients: Sequence[str],
        *,
        port: int = 587,
        user: str = "",
        password: str = "",
        sender: str = "",
        security: str = "starttls",
        timeout: float = 20.0,
    ) -> None:
        self.host = host.strip()
        self.recipients = tuple(r.strip() for r in recipients if r.strip())
        self.port = int(port)
        self.user = user.strip()
        self.password = password
        self.sender = (sender or self.user).strip()
        self.security = security.strip().lower() or "starttls"
        self.timeout = float(timeout)
        if not self.host or not self.recipients or not self.sender:
            raise ValueError("SMTP_HOST, ALERT_EMAIL_TO and SMTP_FROM are required")
        if self.security not in ("starttls", "ssl", "none"):
            raise ValueError("SMTP_SECURITY must be starttls, ssl or none")

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = _single_line(subject)
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(body)
        try:
            if self.security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp as session:
                if self.security == "starttls":
                    session.starttls(context=ssl.create_default_context())
                if self.user:
                    session.login(self.user, self.password)
                session.send_message(message)
        except Exception as exc:  # noqa: BLE001 - sanitise before the caller logs it
            raise RuntimeError(f"email: {type(exc).__name__}") from None


class TelegramChannel:
    """Telegram bot API sendMessage: free mobile push, private chat."""

    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.timeout = float(timeout)
        if not self.token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, subject: str, body: str) -> None:
        # The token is in the URL; never let the URL reach an exception message.
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = json.dumps({
            "chat_id": self.chat_id,
            "text": f"{_single_line(subject)}\n\n{body}",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"telegram: HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - includes URLError, JSONDecodeError
            raise RuntimeError(f"telegram: {type(exc).__name__}") from None
        if not payload.get("ok"):
            description = _single_line(str(payload.get("description") or "not ok"))[:160]
            raise RuntimeError(f"telegram: {description}")


class NtfyChannel:
    """ntfy.sh (or a self-hosted ntfy) publish endpoint.

    The topic URL is effectively a bearer secret: anyone who knows it can
    read the messages, so pick a long random topic or protect it with a
    token.  NTFY_URL is the full topic URL; NTFY_TOKEN is optional.
    """

    name = "ntfy"

    def __init__(self, url: str, *, token: str = "", timeout: float = 10.0) -> None:
        self.url = url.strip()
        self.token = token.strip()
        self.timeout = float(timeout)
        if not self.url:
            raise ValueError("NTFY_URL is required")

    def send(self, subject: str, body: str) -> None:
        headers = {
            "Title": _single_line(subject),
            "Tags": "chart_with_upwards_trend",
            "Content-Type": "text/plain; charset=utf-8",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read(64)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ntfy: HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - never include the topic URL
            raise RuntimeError(f"ntfy: {type(exc).__name__}") from None


class Notifier:
    """Fan one message out to every configured channel.  Never raises."""

    def __init__(
        self,
        channels: Sequence[Channel] = (),
        *,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.channels = list(channels)
        self._log = log or (lambda _message: None)

    @property
    def enabled(self) -> bool:
        return bool(self.channels)

    def describe(self) -> str:
        return ", ".join(channel.name for channel in self.channels)

    def send(self, subject: str, body: str) -> bool:
        """Send to all channels; true when at least one accepted the message."""
        delivered = False
        for channel in self.channels:
            try:
                channel.send(subject, body)
            except Exception as exc:  # noqa: BLE001 - one bad channel is not fatal
                self._log(f"[notify] {channel.name} failed: {exc}")
            else:
                delivered = True
        return delivered

    def send_trade(self, event: Mapping[str, Any]) -> bool:
        subject, body = format_trade(event)
        return self.send(subject, body)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        log: Callable[[str], None] | None = None,
    ) -> "Notifier":
        """Build enabled channels; an incomplete group is logged and skipped."""
        values = os.environ if env is None else env
        channels: list[Channel] = []
        skipped: list[str] = []

        def value(name: str) -> str:
            return str(values.get(name) or "").strip()

        token = value("TELEGRAM_BOT_TOKEN")
        chat_id = value("TELEGRAM_CHAT_ID")
        if token or chat_id:
            try:
                channels.append(TelegramChannel(token, chat_id))
            except ValueError as exc:
                skipped.append(f"telegram ignored ({exc})")

        ntfy_url = value("NTFY_URL")
        if ntfy_url:
            channels.append(NtfyChannel(ntfy_url, token=value("NTFY_TOKEN")))

        smtp_host = value("SMTP_HOST")
        recipients = [
            part.strip() for part in value("ALERT_EMAIL_TO").split(",") if part.strip()
        ]
        if smtp_host or recipients:
            try:
                channels.append(EmailChannel(
                    smtp_host,
                    recipients,
                    port=int(value("SMTP_PORT") or "587"),
                    user=value("SMTP_USER"),
                    password=value("SMTP_PASSWORD"),
                    sender=value("SMTP_FROM"),
                    security=value("SMTP_SECURITY") or "starttls",
                ))
            except ValueError as exc:
                skipped.append(f"email ignored ({exc})")

        logger = log or print
        for warning in skipped:
            logger(f"[notify] {warning}")
        return cls(channels, log=logger)


__all__ = [
    "Channel",
    "EmailChannel",
    "Notifier",
    "NtfyChannel",
    "TelegramChannel",
    "format_trade",
]

