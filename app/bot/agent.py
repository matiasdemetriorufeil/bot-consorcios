"""Conversational agent: provider-independent tool loop.

One incoming message -> at most MAX_ROUNDS model calls. Tools run here (app.bot.tools) with
the phone set by the code. Each model call logs its token usage (and estimated cost) in
bot_events. If the provider fails or the loop runs out, the person gets a fixed message and
the conversation is handed off. A handoff is only recorded in AgentReply.handoff: the channel
carries it out after sending the reply.

History: the caller passes it each time (app.chatwoot reads it from the Chatwoot
conversation; the CLI keeps it in memory). It is trimmed to the last HISTORY_MESSAGES
user/assistant messages, keeping the tool exchanges in between (so unit ids found earlier are
not lost).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.bot import tools
from app.bot.bot_config import load_bot_config
from app.bot.identity import identify_by_phone
from app.bot.prompts import (
    SYSTEM_PROMPT,
    build_user_turn,
    describe_office_hours,
    handoff_notice,
    is_office_hours,
    urgent_handoff_notice,
)
from app.bot.tools import TOOLS, Handoff, ToolContext, run_tool
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
# Followed by the handoff notice (which depends on office hours).
FALLBACK_REPLY = "Tuve un problema técnico."


@dataclass
class AgentReply:
    text: str
    history: list[Message]
    handed_off: bool = False
    handoff: Handoff | None = None
    error: str | None = None
    usage: list[Usage] = field(default_factory=list)
    # (tool name, result status) in call order, for logs and the CLI.
    tools_called: list[tuple[str, str | None]] = field(default_factory=list)


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
        now = self._now().astimezone(ZoneInfo(self.settings.timezone))
        # Admin panel values over .env, re-read at most every minute.
        cfg = load_bot_config(session, self.settings)
        hours = cfg.hours
        ctx = ToolContext(
            session=session,
            phone=phone,
            refresh_debt=self._refresh_debt,
            timezone=self.settings.timezone,
            email_sender=self._email_sender,
            conversation_id=conversation_id,
            handoff_notice=handoff_notice(now, *hours, cfg.out_of_hours_text),
            urgent_handoff_notice=urgent_handoff_notice(
                now, *hours, cfg.emergency_contact_text, cfg.out_of_hours_text
            ),
            payment_how_to=cfg.payment_code_how_to,
            autogestion_url=cfg.autogestion_url,
            office_hours_text=describe_office_hours(*hours),
            emergency_contact=cfg.emergency_contact_text,
        )
        user_turn = build_user_turn(
            text,
            who=identify_by_phone(session, phone),
            now=now,
            office_hours=is_office_hours(now, *hours),
            hours_text=describe_office_hours(*hours),
            first_message=not any(isinstance(m, UserMessage) for m in past),
            welcome_message=cfg.welcome_message,
        )
        # The stored history keeps the plain text; the context only goes in this call.
        turn: list[Message] = []
        usages: list[Usage] = []
        called: list[tuple[str, str | None]] = []

        for round_number in range(1, MAX_ROUNDS + 1):
            messages = past + [UserMessage(user_turn)] + turn
            try:
                response = self.provider.generate(SYSTEM_PROMPT, messages, TOOLS)
            except Exception as exc:
                logger.warning("LLM call failed: %s", type(exc).__name__)
                error = f"provider_error:{type(exc).__name__}"
                return self._fail(ctx, past, text, usages, called, error)
            usages.append(response.usage)
            self._log_usage(ctx, response.usage, round_number)
            message = response.message
            turn.append(message)

            if not message.tool_calls:
                if not message.text:
                    return self._fail(ctx, past, text, usages, called, "empty_answer")
                self._log_turn(ctx, usages, round_number)
                return AgentReply(
                    text=message.text,
                    history=past + [UserMessage(text)] + turn,
                    handed_off=ctx.handoff is not None,
                    handoff=ctx.handoff,
                    usage=usages,
                    tools_called=called,
                )

            results = []
            for call in message.tool_calls:
                content = run_tool(ctx, call.name, call.arguments)
                called.append((call.name, content.get("status")))
                results.append(ToolResult(call_id=call.id, name=call.name, content=content))
            turn.append(ToolResultsMessage(tuple(results)))

        return self._fail(ctx, past, text, usages, called, "max_rounds")

    def _fail(
        self,
        ctx: ToolContext,
        past: list[Message],
        text: str,
        usages: list[Usage],
        called: list[tuple[str, str | None]],
        reason: str,
    ) -> AgentReply:
        ctx.log("agent_error", reason=reason, provider=self.provider.name)
        self._log_turn(ctx, usages, len(usages), error=reason)
        if ctx.handoff is None:
            try:
                tools.handoff_to_human(
                    ctx,
                    reason="technical_error",
                    summary=f"El bot no pudo responder ({reason}). Último mensaje: {text[:500]}",
                    priority="normal",
                )
            except Exception:
                logger.exception("Handoff after an agent error failed")
            if ctx.handoff is None:  # the log failed: hand off anyway
                ctx.handoff = Handoff("technical_error", f"El bot no pudo responder ({reason}).")
        reply = f"{FALLBACK_REPLY} {ctx.handoff_notice}"
        return AgentReply(
            text=reply,
            history=past + [UserMessage(text), AssistantMessage(reply)],
            handed_off=True,
            handoff=ctx.handoff,
            error=reason,
            usage=usages,
            tools_called=called,
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
        cost = self.prices.estimate(total)
        logger.info(
            "Conversation %s: agent turn %s/%s, %d calls, tokens in %d out %d, cost %s%s",
            ctx.conversation_id, self.provider.name, self.provider.model, rounds,
            total.input_tokens, total.output_tokens,
            f"US$ {cost:.5f}" if cost is not None else "n/a",
            f", error {error}" if error else "",
        )  # fmt: skip
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
