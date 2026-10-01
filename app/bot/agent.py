"""Conversational agent: provider-independent tool loop.

One incoming message -> at most MAX_ROUNDS model calls. Tools run here (app.bot.tools) with
the phone set by the code. Each model call logs its token usage (and estimated cost) in
bot_events. If the provider fails or the loop runs out, the person gets a fixed message and
the conversation is handed off.

History: the caller keeps it (Stage 4 will store it per Chatwoot conversation) and passes it
back each time. It is trimmed to the last HISTORY_MESSAGES user/assistant messages, keeping
the tool exchanges in between (so unit ids found earlier are not lost).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.bot import tools
from app.bot.identity import identify_by_phone
from app.bot.prompts import SYSTEM_PROMPT, build_user_turn, is_office_hours
from app.bot.tools import TOOLS, ToolContext, run_tool
from app.config import Settings, get_settings
from app.llm import (
    AssistantMessage,
    LLMProvider,
    Message,
    Prices,
    ToolResult,
    ToolResultsMessage,
    Usage,
    UserMessage,
)
from app.notify.email import EmailSender
from app.sync.live import DebtResult, refresh_unit

logger = logging.getLogger(__name__)

MAX_ROUNDS = 6
HISTORY_MESSAGES = 20
FALLBACK_REPLY = "Tuve un problema técnico, te paso con una persona del estudio."


@dataclass
class AgentReply:
    text: str
    history: list[Message]
    handed_off: bool = False
    error: str | None = None
    usage: list[Usage] = field(default_factory=list)


def _is_conversation_message(message: Message) -> bool:
    if isinstance(message, UserMessage):
        return True
    return isinstance(message, AssistantMessage) and not message.tool_calls


def trim_history(history: list[Message], limit: int = HISTORY_MESSAGES) -> list[Message]:
    """The last `limit` user/assistant messages, with the tool exchanges between them.
    Always starts at a user message (never at an orphan tool result)."""
    count = 0
    start = len(history)
    for i in range(len(history) - 1, -1, -1):
        if _is_conversation_message(history[i]):
            count += 1
            if count > limit:
                break
        start = i
    while start < len(history) and not isinstance(history[start], UserMessage):
        start += 1
    return list(history[start:])


class Agent:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        prices: Prices | None = None,
        settings: Settings | None = None,
        refresh_debt: Callable[[int], DebtResult] = refresh_unit,
        email_sender: EmailSender | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.prices = prices or Prices()
        self.settings = settings or get_settings()
        self._refresh_debt = refresh_debt
        self._email_sender = email_sender
        tz = ZoneInfo(self.settings.timezone)
        self._now = now or (lambda: datetime.now(tz))

    def reply(
        self,
        session: Session,
        phone: str,
        text: str,
        history: list[Message] | None = None,
        *,
        conversation_id: int | None = None,
    ) -> AgentReply:
        past = trim_history(list(history or []))
        ctx = ToolContext(
            session=session,
            phone=phone,
            refresh_debt=self._refresh_debt,
            timezone=self.settings.timezone,
            email_sender=self._email_sender,
            conversation_id=conversation_id,
        )
        now = self._now().astimezone(ZoneInfo(self.settings.timezone))
        s = self.settings
        user_turn = build_user_turn(
            text,
            who=identify_by_phone(session, phone),
            now=now,
            office_hours=is_office_hours(
                now, s.office_hours_start, s.office_hours_end, s.office_weekdays
            ),
            hours_text=f"{s.office_hours_start} a {s.office_hours_end} en días hábiles",
            first_message=not any(isinstance(m, UserMessage) for m in past),
        )
        # The stored history keeps the plain text; the context only goes in this call.
        turn: list[Message] = []
        usages: list[Usage] = []
        handed_off = False

        for round_number in range(1, MAX_ROUNDS + 1):
            messages = past + [UserMessage(user_turn)] + turn
            try:
                response = self.provider.generate(SYSTEM_PROMPT, messages, TOOLS)
            except Exception as exc:
                logger.warning("LLM call failed: %s", type(exc).__name__)
                return self._fail(ctx, past, text, usages, f"provider_error:{type(exc).__name__}")
            usages.append(response.usage)
            self._log_usage(ctx, response.usage, round_number)
            message = response.message
            turn.append(message)

            if not message.tool_calls:
                if not message.text:
                    return self._fail(ctx, past, text, usages, "empty_answer", handed_off)
                self._log_turn(ctx, usages, round_number)
                return AgentReply(
                    text=message.text,
                    history=past + [UserMessage(text)] + turn,
                    handed_off=handed_off,
                    usage=usages,
                )

            results = []
            for call in message.tool_calls:
                content = run_tool(ctx, call.name, call.arguments)
                if call.name == "handoff_to_human" and content.get("status") == "ok":
                    handed_off = True
                results.append(ToolResult(call_id=call.id, name=call.name, content=content))
            turn.append(ToolResultsMessage(tuple(results)))

        return self._fail(ctx, past, text, usages, "max_rounds", handed_off)

    def _fail(
        self,
        ctx: ToolContext,
        past: list[Message],
        text: str,
        usages: list[Usage],
        reason: str,
        already_handed_off: bool = False,
    ) -> AgentReply:
        ctx.log("agent_error", reason=reason, provider=self.provider.name)
        self._log_turn(ctx, usages, len(usages), error=reason)
        if not already_handed_off:
            try:
                tools.handoff_to_human(
                    ctx,
                    reason="technical_error",
                    summary=f"El bot no pudo responder ({reason}). Último mensaje: {text[:500]}",
                    priority="normal",
                )
            except Exception:
                logger.exception("Handoff after an agent error failed")
        return AgentReply(
            text=FALLBACK_REPLY,
            history=past + [UserMessage(text), AssistantMessage(FALLBACK_REPLY)],
            handed_off=True,
            error=reason,
            usage=usages,
        )

    def _log_usage(self, ctx: ToolContext, usage: Usage, round_number: int) -> None:
        ctx.log(
            "llm_usage",
            provider=self.provider.name,
            model=self.provider.model,
            round=round_number,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=self.prices.estimate(usage),
        )

    def _log_turn(
        self, ctx: ToolContext, usages: list[Usage], rounds: int, error: str | None = None
    ) -> None:
        total = Usage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            cache_read_tokens=sum(u.cache_read_tokens for u in usages),
            cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        )
        ctx.log(
            "agent_turn",
            provider=self.provider.name,
            model=self.provider.model,
            rounds=rounds,
            input_tokens=total.input_tokens,
            output_tokens=total.output_tokens,
            cache_read_tokens=total.cache_read_tokens,
            cache_write_tokens=total.cache_write_tokens,
            cost_usd=self.prices.estimate(total),
            error=error,
        )
