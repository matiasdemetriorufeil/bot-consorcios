"""Automatic evaluation of the bot against a real LLM, over invented data.

Builds the database <DB>_eval (migrations + evals/seed.py), runs every case of
evals/cases.yaml in its own transaction (rolled back afterwards) and writes
evals/reports/<date>_<provider>_<model>.md. Debt comes from the stored snapshots and
verification emails are captured: neither ConsorPlus nor any email server is touched.

    uv run python -m evals.run                        # LLM_PROVIDER / LLM_MODEL of .env
    uv run python -m evals.run --provider anthropic --model claude-haiku-4-5
    uv run python -m evals.run --only deuda_simple urgencia_gas --workers 1
"""

import argparse
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.bot.choices import with_options
from app.bot.debt_message import amounts_not_in
from app.bot.tools import TOOLS_BY_NAME
from app.config import Settings, get_settings
from app.db.models import BotEvent, VerificationCode
from app.db.queries import get_latest_debt
from app.llm import (
    AssistantMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Prices,
    ToolSpec,
    Usage,
    UserMessage,
    get_prices,
    get_provider,
)
from app.sync.live import DebtResult
from evals.seed import PHONES, seed

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "cases.yaml"
REPORTS_DIR = ROOT / "evals" / "reports"
TZ = ZoneInfo("America/Argentina/Cordoba")
DEFAULT_NOW = datetime(2026, 10, 1, 11, 0, tzinfo=TZ)  # Thursday, office hours

# USD per million tokens (checked 2026-09-30 on the providers' pricing pages).
PRICES = {
    "gemini-3.8-flash": Prices(input=0.75, output=3.75, cache_read=0.075),
    "claude-haiku-4-5": Prices(input=1, output=5, cache_read=0.10, cache_write=1.25),
}
MAX_TURNS = 6  # a claim reported step by step takes 5
# In the transcripts: a debt message built by the code (the agent's text is 🤖).
DEBT_MARK = "🧾"


# --- Cases ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    text: str
    before: str | None = None


@dataclass(frozen=True)
class Expect:
    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()
    handoff: bool | None = None
    handoff_priority: str | None = None
    contains: tuple[str | tuple[str, ...], ...] = ()
    not_contains: tuple[str, ...] = ()
    not_matches: tuple[str, ...] = ()  # regexes over the normalized replies
    # (tool, status): at least one call of that tool returned that status.
    must_return: tuple[tuple[str, str], ...] = ()
    # (tool, status): no call of that tool returned that status (e.g. book_sum booked).
    must_not_return: tuple[tuple[str, str], ...] = ()
    # The debt messages built by the code (not the agent's text).
    debt_message_contains: tuple[str | tuple[str, ...], ...] = ()
    debt_messages: int | None = None  # how many in the whole case
    # Options to tap (offer_choices): whether some turn offered any, and titles offered.
    choices: bool | None = None
    choices_contain: tuple[str | tuple[str, ...], ...] = ()
    # Only the agent's own text (not the debt messages built by the code).
    reply_not_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    phone: str  # alias
    turns: tuple[Turn, ...]
    expect: Expect
    now: datetime = DEFAULT_NOW
    # Settings changed for this case only, e.g. (("emergency_contact_text", "..."),).
    settings: tuple[tuple[str, Any], ...] = ()


_EXPECT_KEYS = {f for f in Expect.__dataclass_fields__}
_CASE_KEYS = {"id", "category", "phone", "turns", "expect", "now", "settings"}


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    """Parse and validate cases.yaml. Raises ValueError with the case id on any mistake."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases: list[Case] = []
    seen: set[str] = set()
    for item in raw:
        cid = item.get("id", "?")
        try:
            if unknown := set(item) - _CASE_KEYS:
                raise ValueError(f"claves desconocidas {unknown}")
            if cid in seen:
                raise ValueError("id repetido")
            seen.add(cid)
            if item["phone"] not in PHONES:
                raise ValueError(f"teléfono {item['phone']!r} no está en seed.PHONES")
            turns = tuple(
                Turn(t) if isinstance(t, str) else Turn(t["text"], t.get("before"))
                for t in item["turns"]
            )
            if not 1 <= len(turns) <= MAX_TURNS:
                raise ValueError(f"entre 1 y {MAX_TURNS} turnos")
            if any(t.before not in (None, "expire_codes") for t in turns):
                raise ValueError("before solo admite expire_codes")
            exp = item.get("expect") or {}
            if unknown := set(exp) - _EXPECT_KEYS:
                raise ValueError(f"expect con claves desconocidas {unknown}")
            must_return = exp.get("must_return") or {}
            must_not_return = exp.get("must_not_return") or {}
            names = [
                *exp.get("must_call", []),
                *exp.get("must_not_call", []),
                *must_return,
                *must_not_return,
            ]
            for tool in names:
                if tool not in TOOLS_BY_NAME:
                    raise ValueError(f"herramienta inexistente {tool!r}")
            expect = Expect(
                must_call=tuple(exp.get("must_call", [])),
                must_not_call=tuple(exp.get("must_not_call", [])),
                handoff=exp.get("handoff"),
                handoff_priority=exp.get("handoff_priority"),
                contains=tuple(
                    tuple(c) if isinstance(c, list) else c for c in exp.get("contains", [])
                ),
                not_contains=tuple(exp.get("not_contains", [])),
                not_matches=tuple(exp.get("not_matches", [])),
                must_return=tuple(must_return.items()),
                must_not_return=tuple(must_not_return.items()),
                debt_message_contains=tuple(
                    tuple(c) if isinstance(c, list) else c
                    for c in exp.get("debt_message_contains", [])
                ),
                debt_messages=exp.get("debt_messages"),
                choices=exp.get("choices"),
                choices_contain=tuple(
                    tuple(c) if isinstance(c, list) else c for c in exp.get("choices_contain", [])
                ),
                reply_not_contains=tuple(exp.get("reply_not_contains", [])),
            )
            for pattern in expect.not_matches:
                re.compile(pattern)
            now = (
                datetime.fromisoformat(item["now"]).replace(tzinfo=TZ)
                if "now" in item
                else DEFAULT_NOW
            )
            overrides = item.get("settings") or {}
            if unknown := set(overrides) - set(Settings.model_fields):
                raise ValueError(f"settings con claves desconocidas {unknown}")
            cases.append(
                Case(
                    cid, item["category"], item["phone"], turns, expect, now,
                    tuple(overrides.items()),
                )
            )  # fmt: skip
        except (KeyError, TypeError, ValueError, re.error) as exc:
            raise ValueError(f"caso {cid}: {exc}") from exc
    return cases


# --- Checks ---------------------------------------------------------------------------------


def normalize(value: str) -> str:
    """Lower case, no accents, no markdown asterisks, single spaces."""
    decomposed = unicodedata.normalize("NFKD", value.replace("*", ""))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", plain)


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    status: str | None
    turn: int = 0  # index of the user message it answered
    reason: str | None = None


# The bot says a unit has no email. Only right in a turn where start_email_verification
# returned reason "no_owner_email": checked in every case, not only the sin_email ones.
NO_EMAIL_CLAIM = re.compile(
    r"no (tiene|tenes|hay|figura|encuentro|tenemos)( cargado| registrado)?"
    r"( un| ningun| el| algun)? (correo|e-?mail|mail)"
)


@dataclass
class CaseResult:
    case: Case
    user_texts: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)  # the agent's text of each turn
    # The debt messages sent before each reply (one list per turn).
    debt_messages: list[list[str]] = field(default_factory=list)
    # The titles of the options offered with each reply (one list per turn, empty: none).
    choices: list[list[str]] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)
    agent_errors: list[str] = field(default_factory=list)
    crash: str | None = None
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures

    def sent(self, turn: int) -> list[str]:
        """Everything the person got in that turn: debt messages, then the agent's text (with
        the options offered, if any)."""
        before = self.debt_messages[turn] if turn < len(self.debt_messages) else []
        titles = self.choices[turn] if turn < len(self.choices) else []
        reply = self.replies[turn]
        return [*before, with_options(reply, titles) if titles else reply]

    def total_usage(self) -> Usage:
        return Usage(
            input_tokens=sum(u.input_tokens for u in self.usage),
            output_tokens=sum(u.output_tokens for u in self.usage),
            cache_read_tokens=sum(u.cache_read_tokens for u in self.usage),
            cache_write_tokens=sum(u.cache_write_tokens for u in self.usage),
        )


def check(case: Case, result: CaseResult) -> list[str]:
    if result.crash:
        return [f"el runner falló: {result.crash}"]
    failures: list[str] = []
    called = {c.name for c in result.tool_calls}
    exp = case.expect
    failures += [f"no llamó a {t}" for t in exp.must_call if t not in called]
    failures += [f"llamó a {t} (no debía)" for t in exp.must_not_call if t in called]
    for tool, status in exp.must_return:
        got = [c.status for c in result.tool_calls if c.name == tool]
        if status not in got:
            failures.append(f"{tool} no devolvió {status} (devolvió {got})")
    for tool, status in exp.must_not_return:
        if any(c.name == tool and c.status == status for c in result.tool_calls):
            failures.append(f"{tool} devolvió {status} (no debía)")
    handed_off = bool(result.handoffs)
    if exp.handoff is not None and handed_off != exp.handoff:
        failures.append("no derivó (debía)" if exp.handoff else "derivó (no debía)")
    if exp.handoff_priority and handed_off:
        priorities = {h.get("priority") for h in result.handoffs}
        if exp.handoff_priority not in priorities:
            failures.append(f"derivó con prioridad {sorted(priorities)}, no {exp.handoff_priority}")
    bot_text = normalize("\n".join(m for t in range(len(result.replies)) for m in result.sent(t)))
    for wanted in exp.contains:
        options = wanted if isinstance(wanted, tuple) else (wanted,)
        if not any(normalize(o) in bot_text for o in options):
            shown = " | ".join(options)
            failures.append(f"falta en las respuestas: {shown!r}")
    debts = [m for turn in result.debt_messages for m in turn]
    if exp.debt_messages is not None and len(debts) != exp.debt_messages:
        failures.append(f"mandó {len(debts)} mensajes de deuda, no {exp.debt_messages}")
    debt_text = normalize("\n".join(debts))
    for wanted in exp.debt_message_contains:
        options = wanted if isinstance(wanted, tuple) else (wanted,)
        if not any(normalize(o) in debt_text for o in options):
            failures.append(f"falta en el mensaje de deuda: {' | '.join(options)!r}")
    # In every case: once a debt message went out, the agent's text never brings an amount
    # that is not in one of them (a repeated amount is fine, a different one contradicts it).
    for turn, reply in enumerate(result.replies):
        so_far = [m for t in result.debt_messages[: turn + 1] for m in t]
        if so_far and (wrong := amounts_not_in(reply, so_far)):
            failures.append(
                f"el texto del agente del turno {turn + 1} tiene montos que no están en el "
                f"mensaje de deuda: {wrong}"
            )
    for banned in exp.not_contains:
        if normalize(banned) in bot_text:
            failures.append(f"apareció lo prohibido: {banned!r}")
    agent_text = normalize("\n".join(result.replies))
    for banned in exp.reply_not_contains:
        if normalize(banned) in agent_text:
            failures.append(f"el texto del agente dice lo prohibido: {banned!r}")
    offered = [t for turn in result.choices for t in turn]
    if exp.choices is not None and bool(offered) != exp.choices:
        failures.append(
            "no ofreció opciones (debía)" if exp.choices else f"ofreció opciones: {offered}"
        )
    titles = [normalize(t) for t in offered]
    for wanted in exp.choices_contain:
        options = wanted if isinstance(wanted, tuple) else (wanted,)
        if not any(normalize(o) in t for o in options for t in titles):
            failures.append(f"falta entre las opciones: {' | '.join(options)!r} ({offered})")
    for pattern in exp.not_matches:
        if found := re.search(pattern, bot_text):
            failures.append(f"apareció lo prohibido: {found.group(0)!r} (patrón {pattern!r})")
    for turn, reply in enumerate(result.replies):
        said = NO_EMAIL_CLAIM.search(normalize(reply))
        allowed = any(c.turn == turn and c.reason == "no_owner_email" for c in result.tool_calls)
        if said and not allowed:
            failures.append(
                f"dijo {said.group(0)!r} en el turno {turn + 1} sin que "
                "start_email_verification devolviera no_owner_email"
            )
    failures += [f"error del agente: {e}" for e in result.agent_errors]
    return failures


def text_only(history: list[Message]) -> list[Message]:
    """What the next turn gets in production: the stored messages keep only the texts, so
    tool calls and results (unit_ids included) of earlier turns are lost."""
    return [
        m
        for m in history
        if isinstance(m, UserMessage) or (isinstance(m, AssistantMessage) and not m.tool_calls)
    ]


# --- Running --------------------------------------------------------------------------------


_TRANSIENT = (
    "429", "500", "502", "503", "504", "RateLimit", "Overloaded", "ServerError", "Timeout",
    "Connection", "InternalServer", "ResourceExhausted", "Unavailable",
)  # fmt: skip


# Credits or billing ran out (Gemini: 402 "prepayment credits are depleted"; Anthropic: "credit
# balance is too low"): every case after it would fail, so the evaluation stops.
_BILLING = (
    "402",
    "payment required",
    "prepayment",
    "credit balance",
    "billing",
    "insufficient_quota",
)


class RetryingProvider:
    """Retries transient API errors (quota, overload) so they do not count as bot failures.
    A credits or billing error is never retried: it is kept in billing_error and every later
    call fails at once, so the run can stop."""

    def __init__(self, inner: LLMProvider, attempts: int = 6, base_delay: float = 5) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.attempts = attempts
        self.base_delay = base_delay
        self.retries = 0
        self.billing_error: str | None = None
        self._lock = threading.Lock()

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        for attempt in range(self.attempts):
            if self.billing_error:
                raise LLMError(f"evaluación cortada: {self.billing_error}")
            try:
                return self.inner.generate(system, messages, tools)
            except LLMError as exc:
                detail = f"{exc} {type(exc.__cause__).__name__} {exc.__cause__ or ''}"
                if any(b in detail.lower() for b in _BILLING):
                    with self._lock:
                        self.billing_error = self.billing_error or str(exc)
                    raise
                last = attempt == self.attempts - 1
                if last or not any(t.lower() in detail.lower() for t in _TRANSIENT):
                    raise
                with self._lock:
                    self.retries += 1
                time.sleep(self.base_delay * 2**attempt)
        raise AssertionError("unreachable")


def billing_message(provider: RetryingProvider, ran: int, total: int) -> str:
    return (
        f"\nEVALUACIÓN CORTADA: {provider.name} no tiene créditos o hay un problema de "
        f"facturación ({provider.billing_error}).\nSe corrieron {ran} de {total} casos; no se "
        "escribió el informe (los casos afectados fallarían por eso, no por el bot). Revisá "
        "la facturación del proveedor y volvé a correr la evaluación."
    )


@dataclass
class CapturingSender:
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        self.sent.append((to, code))


def _fill(text_: str, sender: CapturingSender) -> str:
    codes = [c for _, c in sender.sent]
    wrong = next(c for c in ("000000", "111111", "222222") if c not in codes)
    return text_.replace("{code}", codes[-1] if codes else "123456").replace("{wrong_code}", wrong)


def run_case(engine: Engine, provider: LLMProvider, settings: Settings, case: Case) -> CaseResult:
    result = CaseResult(case)
    phone = PHONES[case.phone]
    started = time.monotonic()
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            sender = CapturingSender()
            agent = Agent(
                provider,
                settings=settings.model_copy(update=dict(case.settings)),
                refresh_debt=lambda uid: DebtResult(get_latest_debt(session, uid), stale=False),
                email_sender=sender,
                now=lambda: case.now,
            )
            history: list[Message] = []
            last_event = 0
            for index, turn in enumerate(case.turns):
                if turn.before == "expire_codes":
                    session.execute(
                        update(VerificationCode)
                        .where(VerificationCode.phone_e164 == phone)
                        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
                    )
                    session.commit()
                user_text = _fill(turn.text, sender)
                reply = agent.reply(session, phone, user_text, history)
                history = text_only(reply.history)
                result.user_texts.append(user_text)
                result.replies.append(reply.text)
                result.debt_messages.append(list(reply.debt_messages))
                result.choices.append([c.title for c in reply.choices])
                result.usage += reply.usage
                if reply.error:
                    result.agent_errors.append(reply.error)
                events = session.scalars(
                    select(BotEvent)
                    .where(BotEvent.phone_e164 == phone, BotEvent.id > last_event)
                    .order_by(BotEvent.id)
                ).all()
                for event in events:
                    last_event = event.id
                    p = event.payload
                    if event.event_type == "tool_call":
                        result.tool_calls.append(
                            ToolCallRecord(
                                p["tool"], p["args"], p["status"], index, p.get("reason")
                            )
                        )
                    elif event.event_type == "handoff":
                        result.handoffs.append(p)
        except Exception as exc:  # a broken case must not stop the whole evaluation
            result.crash = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()
            transaction.rollback()
    result.seconds = time.monotonic() - started
    result.failures = check(case, result)
    return result


# --- Database -------------------------------------------------------------------------------


def eval_database_url(settings: Settings) -> str:
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_eval").render_as_string(hide_password=False)


def prepare_database(url: str) -> Engine:
    """Fresh <db>_eval: schema from the real migrations, then the invented seed."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    with Session(engine) as session:
        seed(session)
    return engine


# --- Report ---------------------------------------------------------------------------------


def _money(value: float | None) -> str:
    return "sin precio" if value is None else f"US$ {value:.4f}"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", name)


def _transcript(r: CaseResult) -> list[str]:
    lines = ["Herramientas:", ""]
    lines += [
        f"- `{c.name}({c.args})` → {c.status}" + (f" ({c.reason})" if c.reason else "")
        for c in r.tool_calls
    ] or ["- ninguna"]
    if r.handoffs:
        lines.append(
            "- derivaciones: "
            + "; ".join(f"{h.get('priority')}: {h.get('reason')}" for h in r.handoffs)
        )
    lines += ["", f"Conversación ({DEBT_MARK} = mensaje de deuda armado por el código):", ""]
    for turn, user in enumerate(r.user_texts[: len(r.replies)]):
        lines.append(f"> 👤 {user}")
        sent = r.sent(turn)
        for index, bot in enumerate(sent):
            mark = "🤖" if index == len(sent) - 1 else DEBT_MARK
            lines.append(">")
            lines += [f"> {mark} {line}" if line else ">" for line in bot.splitlines()]
        lines.append("")
    return lines


def write_report(
    results: list[CaseResult],
    provider: LLMProvider,
    prices: Prices,
    seconds: float,
    retries: int,
    out_dir: Path = REPORTS_DIR,
    today: date | None = None,
) -> Path:
    today = today or date.today()
    passed = sum(r.passed for r in results)
    total = len(results)
    usage = Usage(
        input_tokens=sum(r.total_usage().input_tokens for r in results),
        output_tokens=sum(r.total_usage().output_tokens for r in results),
        cache_read_tokens=sum(r.total_usage().cache_read_tokens for r in results),
        cache_write_tokens=sum(r.total_usage().cache_write_tokens for r in results),
    )
    cost = prices.estimate(usage)
    calls = sum(len(r.usage) for r in results)
    lines = [
        f"# Evaluación del bot: {provider.name} / {provider.model}",
        "",
        f"- Fecha: {today:%Y-%m-%d}",
        f"- **Aprobados: {passed}/{total} ({100 * passed / max(total, 1):.1f}%)**",
        f"- Costo total: **{_money(cost)}** "
        f"({_money(cost / total if cost is not None and total else None)} por caso)",
        f"- Tokens: entrada {usage.input_tokens:,} · salida {usage.output_tokens:,} · "
        f"cache leída {usage.cache_read_tokens:,} · cache escrita {usage.cache_write_tokens:,} "
        f"· {calls} llamadas al modelo",
        f"- Duración: {seconds:.0f} s · reintentos por errores transitorios de la API: {retries}",
        "",
        "## Por categoría",
        "",
        "| Categoría | Aprobados |",
        "|---|---|",
    ]
    categories: dict[str, list[CaseResult]] = {}
    for r in results:
        categories.setdefault(r.case.category, []).append(r)
    for category, items in categories.items():
        ok = sum(r.passed for r in items)
        lines.append(f"| {category} | {ok}/{len(items)} |")
    lines += [
        "",
        "## Casos",
        "",
        "| Caso | Categoría | Resultado | Herramientas | Costo |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        tools = ", ".join(c.name for c in r.tool_calls) or "-"
        mark = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.case.id} | {r.case.category} | {mark} | {tools} | "
            f"{_money(prices.estimate(r.total_usage()))} |"
        )
    failed = [r for r in results if not r.passed]
    lines += ["", "## Fallas", ""]
    if not failed:
        lines.append("Ninguna.")
    for r in failed:
        lines += [f"### {r.case.id} ({r.case.category})", ""]
        lines += [f"- {f}" for f in r.failures]
        lines += ["", *_transcript(r)]
    lines += ["## Todas las conversaciones", ""]
    for r in results:
        mark = "✅" if r.passed else "❌"
        lines += [
            "<details>",
            f"<summary>{mark} {r.case.id} ({r.case.category})</summary>",
            "",
            *_transcript(r),
            "</details>",
            "",
        ]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today:%Y-%m-%d}_{provider.name}_{_safe(provider.model)}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- Main -----------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["gemini", "anthropic"])
    parser.add_argument("--model", default=None, help="vacío = default del proveedor")
    parser.add_argument("--only", nargs="+", metavar="ID", help="solo estos casos")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--database-url", help="por defecto: DATABASE_URL con sufijo _eval")
    args = parser.parse_args()

    base = get_settings()
    overrides: dict[str, Any] = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
        overrides["llm_model"] = args.model or ""
    elif args.model:
        overrides["llm_model"] = args.model
    settings = base.model_copy(update=overrides)
    provider = RetryingProvider(get_provider(settings))
    prices = PRICES.get(provider.model) or (
        get_prices(base)
        if (base.llm_provider, base.llm_model) == (settings.llm_provider, settings.llm_model)
        else Prices()
    )

    cases = load_cases()
    if args.only:
        missing = set(args.only) - {c.id for c in cases}
        if missing:
            parser.error(f"casos inexistentes: {', '.join(sorted(missing))}")
        cases = [c for c in cases if c.id in args.only]

    engine = prepare_database(args.database_url or eval_database_url(base))
    print(f"{provider.name} / {provider.model}: {len(cases)} casos, {args.workers} en paralelo")
    started = time.monotonic()
    done = 0
    lock = threading.Lock()

    def one(case: Case) -> CaseResult | None:
        nonlocal done
        if provider.billing_error:
            return None  # the run is stopping: do not even start it
        result = run_case(engine, provider, settings, case)
        with lock:
            done += 1
            mark = "ok  " if result.passed else "FAIL"
            print(f"[{done:>3}/{len(cases)}] {mark} {case.id}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        ran = list(pool.map(one, cases))
    engine.dispose()
    if provider.billing_error:
        print(billing_message(provider, sum(r is not None for r in ran), len(cases)))
        return 2
    results = [r for r in ran if r is not None]

    path = write_report(results, provider, prices, time.monotonic() - started, provider.retries)
    passed = sum(r.passed for r in results)
    cost = prices.estimate(Usage(
        input_tokens=sum(r.total_usage().input_tokens for r in results),
        output_tokens=sum(r.total_usage().output_tokens for r in results),
        cache_read_tokens=sum(r.total_usage().cache_read_tokens for r in results),
        cache_write_tokens=sum(r.total_usage().cache_write_tokens for r in results),
    ))  # fmt: skip
    print(f"\nAprobados {passed}/{len(results)} · costo {_money(cost)} · informe: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
