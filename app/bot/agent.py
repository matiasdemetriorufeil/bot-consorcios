"""Conversational agent: provider-independent tool loop.

One incoming message -> at most MAX_ROUNDS model calls. Tools run here (app.bot.tools) with
the phone set by the code. Each model call logs its token usage (and estimated cost) in
bot_events. If the provider fails or the loop runs out, the person gets a fixed message and
the conversation is handed off. A handoff is only recorded in AgentReply.handoff: the channel
carries it out after sending the reply.

Debt: get_debt and get_payment_info build their message themselves (app.bot.debt_message).
The reply carries those blocks in AgentReply.debt_messages and the channel sends them in the
same message as AgentReply.text, before it. In the first message of a conversation the first
one starts with the greeting: the admin panel's welcome_message, or FIRST_GREETING when it
is empty (the model is told it already went). The history keeps them
joined with the text (join_blocks), as the person gets them and the channel reads them back.
"$" amounts in the text that match no debt message are logged (debt_amount_mismatch).

Claims (app.claims.flow): while the phone has a claim draft in a step, the code answers the
message (no model call); so do the menu's "Registrar reclamo" and "Mis reclamos". The claim
tools leave their texts in ToolContext.blocks (safety texts, a list of claims), which go out
like the debt messages: as is, before the agent's text (AgentReply.debt_messages carries both).
A message that is not an answer to the step drops the draft: its notice goes first and the
model answers as usual.

Options: offer_choices ends the turn without another model call. Its text is AgentReply.text
and its options AgentReply.choices; the channel sends them as buttons or a list (or numbered
where it cannot). It runs after the other tools of its round, so a handoff of that round
rejects it. The history keeps the titles offered (app.bot.choices.with_options).

History: the caller passes it each time (the channel reads it from the stored WhatsApp
messages, app.whatsapp.channel; the CLI keeps it in memory). It is trimmed to the last
HISTORY_MESSAGES user/assistant messages, keeping the tool exchanges in between (so unit ids
found earlier are not lost).
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.bot import tools
from app.bot.bot_config import load_bot_config
from app.bot.choices import Choice, with_options
from app.bot.claim_tools import my_claims_text
from app.bot.debt_message import FIRST_GREETING, amounts_not_in, join_blocks
from app.bot.identity import identify_by_phone, to_e164
from app.bot.prompts import (
    SYSTEM_PROMPT,
    build_user_turn,
    describe_office_hours,
    handoff_notice,
    is_office_hours,
    urgent_handoff_notice,
)
from app.bot.tools import TOOLS, Handoff, ToolContext, run_tool
from app.claims import flow as claim_flow
from app.claims import texts as claim_texts
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
# The menu's options the code answers by itself (normalized, app.claims.flow.normalize).
REPORT_CLAIM = "registrar reclamo"
MY_CLAIMS = "mis reclamos"
HANDOFF_OFFER = (Choice("Sí, pasame", "Sí, pasame"), Choice("No, gracias", "No, gracias"))


@dataclass
class AgentReply:
    text: str
    history: list[Message]
    # Debt messages built by the code (one per unit), sent as is BEFORE text.
    debt_messages: list[str] = field(default_factory=list)
    # Options to tap that go with text (offer_choices). Empty: a plain text reply.
    choices: tuple[Choice, ...] = ()
    handed_off: bool = False
    handoff: Handoff | None = None
    error: str | None = None
    usage: list[Usage] = field(default_factory=list)
    # (tool name, result status) in call order, for logs and the CLI.
    tools_called: list[tuple[str, str | None]] = field(default_factory=list)


def _attachment_note(types: Sequence[str]) -> str:
    kinds = ", ".join(sorted(set(types)))
    return (
        f"\n\n[La persona además mandó un adjunto ({kinds}) que vos no podés ver ni escuchar; "
        "si derivás, el estudio sí lo ve.]"
    )


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
        attachment_ids: Sequence[int] = (),
        attachment_types: Sequence[str] = (),
    ) -> AgentReply:
        """attachment_ids: the stored WhatsApp messages with an image (photos of a claim);
        attachment_types: what the person attached (the model is told it cannot see them)."""
        past = trim_history(list(history or []))
        now = self._now().astimezone(ZoneInfo(self.settings.timezone))
        # Admin panel values over .env, re-read at most every minute.
        cfg = load_bot_config(session, self.settings)
        hours = cfg.hours
        first_message = not any(isinstance(m, UserMessage) for m in past)
        greeting = cfg.welcome_message.strip() or FIRST_GREETING
        notices: list[str] = []
        if to_e164(phone):
            code = self._claim_turn(
                session, phone, text, attachment_ids, conversation_id, now, notices
            )
            if code is not None:
                reply_text, choices, blocks = code
                return self._code_reply(
                    past, text, reply_text, choices, blocks, greeting if first_message else None
                )
        note = _attachment_note(attachment_types) if attachment_types else ""
        text_for_model = text + note
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
            first_message=first_message,
            greeting=greeting,
            now=now,
            user_text=text,
            person_texts=[m.text for m in past if isinstance(m, UserMessage)] + [text],
            last_bot_text=next(
                (
                    m.text
                    for m in reversed(past)
                    if _is_conversation_message(m) and isinstance(m, AssistantMessage)
                ),
                "",
            ),  # fmt: skip
        )
        user_turn = build_user_turn(
            text_for_model,
            who=identify_by_phone(session, phone),
            now=now,
            office_hours=is_office_hours(now, *hours),
            hours_text=describe_office_hours(*hours),
            first_message=first_message,
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
                return self._done(ctx, past, text, turn, usages, called, round_number, notices)

            results: dict[str, ToolResult] = {}
            # offer_choices last: it ends the turn, and a handoff of this round rejects it.
            ordered = sorted(message.tool_calls, key=lambda c: c.name == "offer_choices")
            for call in ordered:
                content = run_tool(ctx, call.name, call.arguments)
                called.append((call.name, content.get("status")))
                results[call.id] = ToolResult(call_id=call.id, name=call.name, content=content)
            turn.append(ToolResultsMessage(tuple(results[c.id] for c in message.tool_calls)))
            if ctx.offer is not None:
                turn.append(AssistantMessage(ctx.offer.text))
                return self._done(ctx, past, text, turn, usages, called, round_number, notices)

        return self._fail(ctx, past, text, usages, called, "max_rounds")

    # --- Claims, answered by the code ------------------------------------------------------

    def _claim_turn(
        self,
        session: Session,
        phone: str,
        text: str,
        attachment_ids: Sequence[int],
        conversation_id: int | None,
        now: datetime,
        notices: list[str],
    ) -> tuple[str, tuple[Choice, ...], list[str]] | None:
        """(text, options, blocks) when the code answers this message (a claim step or the
        menu's claim options), else None (notices may get a "dropped"/"expired" notice)."""
        asked = claim_flow.normalize(text)
        known = identify_by_phone(session, phone).known
        if asked == REPORT_CLAIM and known:
            started = claim_flow.start(session, phone, conversation_id=conversation_id, now=now)
            if started.status == "not_available":
                return started.text, HANDOFF_OFFER, started.blocks
            if started.status == "ok":
                return started.text, started.choices, started.blocks
        if asked == MY_CLAIMS and known:
            listed = my_claims_text(session, phone, now)
            if listed is not None:
                return listed, (), []
        draft, expired = claim_flow.active_draft(session, phone, now)
        if expired:
            notices.append(claim_texts.EXPIRED)
        if draft is not None and claim_flow.in_steps(draft):
            step = claim_flow.handle(session, draft, text, attachment_ids, now=now)
            if step.handled:
                return step.text, step.choices, step.blocks
            notices.extend(step.blocks)
        return None

    def _code_reply(
        self,
        past: list[Message],
        text: str,
        reply: str,
        choices: tuple[Choice, ...],
        blocks: list[str],
        greeting: str | None,
    ) -> AgentReply:
        """A turn answered by the code, without the model (no tokens)."""
        blocks = list(blocks)
        if greeting:
            if blocks:
                blocks[0] = join_blocks([greeting], blocks[0])
            else:
                blocks = [greeting]
        shown = with_options(reply, [c.title for c in choices]) if choices else reply
        return AgentReply(
            text=reply,
            debt_messages=blocks,
            choices=choices,
            history=[*past, UserMessage(text), AssistantMessage(join_blocks(blocks, shown))],
        )

    def _done(
        self,
        ctx: ToolContext,
        past: list[Message],
        text: str,
        turn: list[Message],
        usages: list[Usage],
        called: list[tuple[str, str | None]],
        rounds: int,
        notices: Sequence[str] = (),
    ) -> AgentReply:
        """The reply of a turn that ended well; turn[-1] is the agent's text."""
        self._log_turn(ctx, usages, rounds)
        last = turn[-1]
        reply = last.text if isinstance(last, AssistantMessage) else ""
        debt_only = list(ctx.debt_messages.values())
        if debt_only:
            self._check_amounts(ctx, reply, debt_only)
        debt_messages = [*notices, *debt_only, *ctx.blocks]
        if debt_messages and ctx.first_message:
            debt_messages[0] = join_blocks([ctx.greeting], debt_messages[0])
        elif ctx.first_message and ctx.code_reply:
            debt_messages = [ctx.greeting]  # a claim step built by the code: greet first
        choices = ctx.offer.choices if ctx.offer is not None else ()
        if debt_messages or choices:
            # As the person sees it (and as the history gives it back next time).
            shown = with_options(reply, [c.title for c in choices]) if choices else reply
            turn[-1] = AssistantMessage(join_blocks(debt_messages, shown))
        return AgentReply(
            text=reply,
            debt_messages=debt_messages,
            choices=choices,
            history=past + [UserMessage(text)] + turn,
            handed_off=ctx.handoff is not None,
            handoff=ctx.handoff,
            usage=usages,
            tools_called=called,
        )

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
            # A safety text the code already built still goes out.
            debt_messages=list(ctx.blocks),
            history=past + [UserMessage(text), AssistantMessage(reply)],
            handed_off=True,
            handoff=ctx.handoff,
            error=reason,
            usage=usages,
            tools_called=called,
        )

    def _check_amounts(self, ctx: ToolContext, text: str, debt_messages: list[str]) -> None:
        """The model was told not to repeat amounts; one that matches no debt message may be
        a wrong one. Not blocked (the debt message already went out), only logged."""
        if wrong := amounts_not_in(text, debt_messages):
            logger.warning(
                "Conversation %s: %d amount(s) in the agent text match no debt message",
                ctx.conversation_id, len(wrong),
            )  # fmt: skip
            ctx.log("debt_amount_mismatch", amounts=wrong, debt_messages=len(debt_messages))

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
