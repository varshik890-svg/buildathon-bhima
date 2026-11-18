"""Telegram front end for Bhima (python-telegram-bot v22, async API).

Only the owner chat (TELEGRAM_CHAT_ID) is served. Everything else is ignored
silently and logged. Orchestrator calls are blocking, so they run through
asyncio.to_thread and never block the event loop.

Run with `python -m bhima bot` (cli.py calls run_bot()).
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import os
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("bhima.telegram")

MAX_MESSAGE = 4000          # Telegram hard limit is 4096; keep a margin
DOCUMENT_THRESHOLD = 12000  # longer than this -> send as a file instead
APPROVAL_KINDS = {"needs_approval", "plan_ready", "memory_proposal"}
EVENT_ICONS = {
    "plan_ready": "[plan]",
    "task_started": "[start]",
    "task_done": "[done]",
    "needs_approval": "[approve?]",
    "merged": "[merged]",
    "error": "[error]",
    "memory_proposal": "[memory]",
}

HELP_TEXT = (
    "Bhima commands:\n"
    "/ask <request> - plan and run a coding request (plain text works too)\n"
    "/status - what the harness is doing now\n"
    "/report - rounds, tokens and time\n"
    "/memory - current memory snapshot\n"
    "/skills - skills index\n"
    "/scrape <url> [sort key] - extract tables/lists from a page\n"
    "/help - this message"
)


# ---------------------------------------------------------------- config ---

def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_settings() -> tuple[str, str]:
    """Return (token, chat_id). Prefers bhima.config, then env, then .env."""
    token = chat_id = ""
    try:
        cfg = importlib.import_module("bhima.config")
        token = str(getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "")
        chat_id = str(getattr(cfg, "TELEGRAM_CHAT_ID", "") or "")
    except Exception:  # config not ready or broken: fall back
        pass
    dotenv = _read_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "") or dotenv.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "") or dotenv.get("TELEGRAM_CHAT_ID", "")
    return token.strip(), chat_id.strip()


def parse_chat_id(raw: str) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------- pure helpers ---

def is_owner(chat_id: Any, owner_id: int | None) -> bool:
    """True only when an owner id is configured and matches exactly."""
    if owner_id is None or chat_id is None:
        return False
    try:
        return int(chat_id) == int(owner_id)
    except (TypeError, ValueError):
        return False


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split text into chunks <= limit, preferring newline boundaries."""
    if not text:
        return [""]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = limit
            chunks.append(rest[:cut])
            rest = rest[cut:]
        else:
            chunks.append(rest[:cut])
            rest = rest[cut + 1:]
    if rest:
        chunks.append(rest)
    return chunks


def make_callback(action: str, target_id: Any) -> str:
    data = f"{action}:{target_id}"
    if len(data.encode("utf-8")) > 64:  # Telegram callback_data limit
        raise ValueError("callback data too long")
    return data


def parse_callback(data: str | None) -> tuple[str, str] | None:
    """'approve:<id>' -> ('approve', '<id>'); anything else -> None."""
    if not data or ":" not in data:
        return None
    action, _, target = data.partition(":")
    action, target = action.strip().lower(), target.strip()
    if action not in ("approve", "deny") or not target:
        return None
    return action, target


def normalize_event(event: Any) -> dict[str, Any]:
    """Accept a dict or an object with kind/id/text attributes."""
    if isinstance(event, dict):
        data = dict(event)
    else:
        data = {k: getattr(event, k, None) for k in ("kind", "id", "text")}
    data["kind"] = str(data.get("kind") or "event")
    data["id"] = "" if data.get("id") is None else str(data["id"])
    data["text"] = "" if data.get("text") is None else str(data["text"])
    return data


def format_event(event: Any) -> str:
    ev = normalize_event(event)
    icon = EVENT_ICONS.get(ev["kind"], f"[{ev['kind']}]")
    head = f"{icon} {ev['kind']}"
    if ev["id"]:
        head += f" ({ev['id']})"
    return f"{head}\n{ev['text']}".rstrip()


def needs_buttons(event: Any) -> bool:
    ev = normalize_event(event)
    return ev["kind"] in APPROVAL_KINDS and bool(ev["id"])


def looks_like_diff(text: str) -> bool:
    return text.startswith("diff --git") or "\n@@ " in text or text.startswith("--- ")


def parse_scrape_args(args: list[str]) -> tuple[str, str | None] | None:
    if not args:
        return None
    url = args[0]
    if not url.startswith(("http://", "https://")):
        return None
    sort_by = " ".join(args[1:]).strip() or None
    return url, sort_by


def rows_to_text(rows: list[dict], limit: int = 30) -> str:
    if not rows:
        return "No rows found."
    keys: list[str] = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    lines = [" | ".join(keys)]
    for row in rows[:limit]:
        lines.append(" | ".join(str(row.get(k, "")) for k in keys))
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows")
    return "\n".join(lines)


def rows_to_csv(rows: list[dict]) -> str:
    import csv
    keys: list[str] = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in keys})
    return buf.getvalue()


# ------------------------------------------------------ backend adapter ---

class Backend:
    """Lazy bridge to the other bhima modules (owned by other agents).

    Imports happen on first use, so the bot starts even if a module is
    missing; the user then sees a short "not available" message.
    """

    def __init__(self) -> None:
        self._orch: Any = None

    @staticmethod
    def _mod(name: str) -> Any:
        return importlib.import_module(f"bhima.{name}")

    def orchestrator(self) -> Any:
        if self._orch is None:
            mod = self._mod("orchestrator")
            if hasattr(mod, "submit"):
                self._orch = mod
            elif hasattr(mod, "get_orchestrator"):
                self._orch = mod.get_orchestrator()
            elif hasattr(mod, "Orchestrator"):
                self._orch = mod.Orchestrator()
            else:
                raise RuntimeError("bhima.orchestrator has no submit()")
        return self._orch

    def submit(self, request: str) -> str:
        return str(self.orchestrator().submit(request))

    def approve(self, target_id: str) -> Any:
        return self.orchestrator().approve(target_id)

    def deny(self, target_id: str) -> Any:
        return self.orchestrator().deny(target_id)

    def status(self) -> str:
        return str(self.orchestrator().status())

    def on_event(self, callback: Callable[[Any], None]) -> None:
        self.orchestrator().on_event(callback)

    def report(self) -> str:
        mod = self._mod("report")
        for name in ("render", "report", "summary", "render_report"):
            fn = getattr(mod, name, None)
            if callable(fn):
                return str(fn())
        raise RuntimeError("bhima.report has no render()")

    def memory_snapshot(self) -> str:
        return str(self._mod("memory").snapshot())

    def skills_index(self) -> str:
        return str(self._mod("skills").index())

    def scrape(self, url: str, sort_by: str | None) -> list[dict]:
        return list(self._mod("scrape").scrape(url, sort_by))


# ------------------------------------------------------------------- bot ---

class BhimaBot:
    def __init__(self, token: str, owner_id: int | None, backend: Backend | None = None) -> None:
        self.token = token
        self.owner_id = owner_id
        self.backend = backend or Backend()
        self.app: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None

    # ---- auth
    def _allowed(self, update: Any) -> bool:
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        if is_owner(chat_id, self.owner_id):
            return True
        log.warning("ignored update from chat %s", chat_id)
        return False

    # ---- sending
    async def send_text(self, text: str, reply_markup: Any = None, filename: str = "bhima.txt") -> None:
        if self.app is None or self.owner_id is None:
            return
        bot = self.app.bot
        if len(text) > DOCUMENT_THRESHOLD or looks_like_diff(text):
            name = "diff.patch" if looks_like_diff(text) else filename
            await bot.send_document(
                chat_id=self.owner_id,
                document=io.BytesIO(text.encode("utf-8")),
                filename=name,
                caption=text.splitlines()[0][:200] if text else None,
                reply_markup=reply_markup,
            )
            return
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            await bot.send_message(chat_id=self.owner_id, text=chunk,
                                   reply_markup=reply_markup if last else None)

    def approval_keyboard(self, target_id: str) -> Any:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=make_callback("approve", target_id)),
            InlineKeyboardButton("Deny", callback_data=make_callback("deny", target_id)),
        ]])

    async def send_event(self, event: Any) -> None:
        ev = normalize_event(event)
        markup = self.approval_keyboard(ev["id"]) if needs_buttons(ev) else None
        try:
            await self.send_text(format_event(ev), reply_markup=markup)
        except Exception:
            log.exception("failed to send event %s", ev["kind"])

    def on_orchestrator_event(self, event: Any) -> None:
        """Thread-safe: the orchestrator may call this from any thread."""
        if self.loop is None or self.loop.is_closed():
            log.info("event before bot loop ready: %s", normalize_event(event)["kind"])
            return
        asyncio.run_coroutine_threadsafe(self.send_event(event), self.loop)

    async def _reply(self, update: Any, text: str) -> None:
        if len(text) > DOCUMENT_THRESHOLD:
            await self.send_text(text)
            return
        for chunk in split_message(text):
            await update.effective_message.reply_text(chunk)

    async def _call(self, update: Any, fn: Callable, *args: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:
            log.exception("backend call failed")
            await self._reply(update, f"Error: {type(exc).__name__}: {exc}")
            return None

    # ---- handlers
    async def cmd_start(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        await self._reply(update, "Bhima is listening.\n\n" + HELP_TEXT)

    async def cmd_help(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        await self._reply(update, HELP_TEXT)

    async def cmd_ask(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        text = " ".join(getattr(context, "args", None) or []).strip()
        await self._submit(update, text)

    async def on_text(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        await self._submit(update, (update.effective_message.text or "").strip())

    async def _submit(self, update: Any, text: str) -> None:
        if not text:
            await self._reply(update, "Usage: /ask <what you want built>")
            return
        await self._reply(update, "Got it. Planning...")
        # submit may run the whole flow; do not block the loop or the handler
        asyncio.create_task(self._submit_bg(update, text))

    async def _submit_bg(self, update: Any, text: str) -> None:
        request_id = await self._call(update, self.backend.submit, text)
        if request_id is not None:
            await self.send_text(f"Request {request_id} accepted.")

    async def cmd_status(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        out = await self._call(update, self.backend.status)
        if out is not None:
            await self._reply(update, out or "Idle.")

    async def cmd_report(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        out = await self._call(update, self.backend.report)
        if out is not None:
            if len(out) > DOCUMENT_THRESHOLD:
                await self.send_text(out, filename="report.txt")
            else:
                await self._reply(update, out or "No runs yet.")

    async def cmd_memory(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        out = await self._call(update, self.backend.memory_snapshot)
        if out is not None:
            await self._reply(update, out or "Memory is empty.")

    async def cmd_skills(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        out = await self._call(update, self.backend.skills_index)
        if out is not None:
            await self._reply(update, out or "No skills yet.")

    async def cmd_scrape(self, update: Any, context: Any) -> None:
        if not self._allowed(update):
            return
        parsed = parse_scrape_args(list(getattr(context, "args", None) or []))
        if parsed is None:
            await self._reply(update, "Usage: /scrape <http(s)://url> [sort key]")
            return
        url, sort_by = parsed
        await self._reply(update, f"Scraping {url} ...")
        rows = await self._call(update, self.backend.scrape, url, sort_by)
        if rows is None:
            return
        await self._reply(update, f"{len(rows)} rows\n" + rows_to_text(rows))
        if len(rows) > 30:
            await update.effective_message.reply_document(
                document=io.BytesIO(rows_to_csv(rows).encode("utf-8")), filename="scrape.csv")

    async def on_button(self, update: Any, context: Any) -> None:
        query = update.callback_query
        # defensive: the handler filter is not applied to callback queries
        if not self._allowed(update):
            await query.answer()
            return
        parsed = parse_callback(query.data)
        if parsed is None:
            await query.answer("Unknown button")
            return
        action, target = parsed
        await query.answer(f"{action} {target}...")
        fn = self.backend.approve if action == "approve" else self.backend.deny
        try:
            await asyncio.to_thread(fn, target)
            note = "Approved" if action == "approve" else "Denied"
        except Exception as exc:
            log.exception("%s failed", action)
            note = f"{action} failed: {type(exc).__name__}: {exc}"
        try:
            old = query.message.text or query.message.caption or ""
            if query.message.text:
                await query.edit_message_text(f"{old}\n\n-> {note}"[:MAX_MESSAGE])
            else:
                await query.edit_message_reply_markup(reply_markup=None)
                await self.send_text(f"{target}: {note}")
        except Exception:
            await self.send_text(f"{target}: {note}")

    async def on_error(self, update: Any, context: Any) -> None:
        log.error("telegram error: %s", getattr(context, "error", None))

    # ---- app
    def build_app(self) -> Any:
        from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                                  MessageHandler, filters)
        app = Application.builder().token(self.token).build()
        owner = filters.Chat(chat_id=self.owner_id)
        commands = {
            "start": self.cmd_start, "help": self.cmd_help, "ask": self.cmd_ask,
            "status": self.cmd_status, "report": self.cmd_report,
            "memory": self.cmd_memory, "skills": self.cmd_skills,
            "scrape": self.cmd_scrape,
        }
        for name, fn in commands.items():
            app.add_handler(CommandHandler(name, fn, filters=owner))
        app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_error_handler(self.on_error)
        self.app = app
        return app

    async def run(self, stop: asyncio.Event | None = None) -> None:
        self.loop = asyncio.get_running_loop()
        app = self.build_app()
        try:
            self.backend.on_event(self.on_orchestrator_event)
        except Exception as exc:
            log.warning("orchestrator events not wired: %s", exc)
        stop = stop or asyncio.Event()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        me = await app.bot.get_me()
        print(f"Bhima bot @{me.username} running for owner chat. Ctrl-C to stop.")
        try:
            await stop.wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


# ------------------------------------------------------ chat-id discovery ---

async def discover_chat_id(token: str) -> None:
    """No TELEGRAM_CHAT_ID yet: print the chat id of whoever sends /start
    to the terminal (never to Telegram) and refuse everything else."""
    from telegram.ext import Application, MessageHandler, filters

    async def show(update: Any, context: Any) -> None:
        chat = update.effective_chat
        msg = update.effective_message
        if msg is not None and (msg.text or "").strip().startswith("/start"):
            print(f"\n>>> chat id: {chat.id}  -> put TELEGRAM_CHAT_ID={chat.id} in .env and restart\n")
        else:
            log.warning("TELEGRAM_CHAT_ID not set; ignoring update from %s", getattr(chat, "id", None))

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.ALL, show))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("TELEGRAM_CHAT_ID is empty. Send /start to the bot from your account; "
          "the chat id will be printed here. Ctrl-C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def run_bot() -> int:
    """Entry point for `python -m bhima bot`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its URLs contain the token
    token, raw_chat = load_settings()
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing. Add it to .env (see .env.example).")
        return 1
    owner = parse_chat_id(raw_chat) if raw_chat else None
    try:
        if owner is None:
            if raw_chat:
                print("TELEGRAM_CHAT_ID is not a number; running chat-id discovery.")
            asyncio.run(discover_chat_id(token))
        else:
            asyncio.run(BhimaBot(token, owner).run())
    except KeyboardInterrupt:
        print("Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_bot())
