"""
conversation_memory.py - Automatic conversation summarisation and context management.
Supports both LangChain message objects (when installed) and lightweight message objects.
"""

import logging
from typing import Optional, List, Dict, Any

from config import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
    CHAT_DB_PATH,
    SUMMARISE_THRESHOLD as _DEFAULT_THRESHOLD,
    RECENT_WINDOW as _DEFAULT_WINDOW,
)

try:
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
except ImportError:
    class _BaseMsg:
        def __init__(self, content: str, msg_type: str):
            self.content = content
            self.type = msg_type

    class HumanMessage(_BaseMsg):
        def __init__(self, content: str):
            super().__init__(content, "human")

    class AIMessage(_BaseMsg):
        def __init__(self, content: str):
            super().__init__(content, "ai")

    class SystemMessage(_BaseMsg):
        def __init__(self, content: str):
            super().__init__(content, "system")

logger = logging.getLogger(__name__)

DEFAULT_SUMMARISE_THRESHOLD = _DEFAULT_THRESHOLD
DEFAULT_RECENT_WINDOW = _DEFAULT_WINDOW


def _messages_to_lc(raw_messages: list[dict]) -> list:
    """Convert raw DB message dicts to message objects."""
    result = []
    for m in raw_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        elif role == "system":
            result.append(SystemMessage(content=content))
    return result


def _generate_summary_text(
    existing_summary: Optional[str],
    messages_to_summarise: list[dict],
) -> str:
    """
    Produce an updated conversation summary (via Groq LLM when available, or
    concise deterministic summary fallback when offline).
    """
    history_lines = []
    for m in messages_to_summarise:
        role = m.get("role", "user").capitalize()
        content = m.get("content", "")
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines)

    prior_summary_section = (
        f"Existing summary of earlier messages:\n{existing_summary}\n\n"
        if existing_summary
        else ""
    )

    if GROQ_API_KEY:
        try:
            from langchain_groq import ChatGroq
            from langchain_core.prompts import ChatPromptTemplate

            llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0)
            prompt = ChatPromptTemplate.from_template(
                """You are a conversation memory assistant for an AI-powered Invoice Automation system.
Your task is to produce a concise, updated summary that captures the key information from the conversation segment below.

{prior_summary}Conversation segment to summarise:
{history}

Write a summary that preserves:
- The user's goals and any unresolved requests.
- Relevant date ranges and invoice filters mentioned.
- Previously selected invoice groups or report parameters.
- Decisions, constraints, and important clarifications made.

Do NOT include exact invoice amounts in the summary. Keep the summary factual and concise (at most 300 words).

Updated summary:"""
            )
            chain = prompt | llm
            response = chain.invoke({"prior_summary": prior_summary_section, "history": history_text})
            return getattr(response, "content", str(response)).strip()
        except Exception as exc:
            logger.debug("Groq summarisation unavailable, using deterministic summary: %s", exc)

    user_topics = [
        m.get("content", "").strip()[:120]
        for m in messages_to_summarise
        if m.get("role") == "user" and m.get("content")
    ]
    summary_parts = []
    if existing_summary:
        summary_parts.append(existing_summary.strip())
    if user_topics:
        summary_parts.append("Prior topics discussed: " + "; ".join(user_topics[-5:]))
    return " | ".join(summary_parts)[:800]


def maybe_summarise(
    conversation_id: str,
    summarise_threshold: int = DEFAULT_SUMMARISE_THRESHOLD,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    db_path: Optional[str] = None,
) -> None:
    from chat_store import get_all_messages_raw, get_latest_summary, save_summary

    _db = db_path or CHAT_DB_PATH
    all_msgs = get_all_messages_raw(conversation_id, _db)
    total = len(all_msgs)

    if total < summarise_threshold:
        return

    latest_summary = get_latest_summary(conversation_id, _db)
    covered_up_to_id = latest_summary.get("summarized_up_to_message_id") if latest_summary else None
    if covered_up_to_id:
        covered_ids = [m["message_id"] for m in all_msgs]
        try:
            covered_idx = covered_ids.index(covered_up_to_id)
        except ValueError:
            covered_idx = -1
        unsummarised = all_msgs[covered_idx + 1:]
    else:
        unsummarised = all_msgs

    messages_to_cover = unsummarised[: max(0, len(unsummarised) - recent_window)]
    if not messages_to_cover:
        return
    if len(messages_to_cover) < max(2, recent_window // 2):
        return

    existing_summary_text = latest_summary["summary_text"] if latest_summary else None
    last_covered_msg = messages_to_cover[-1]

    try:
        new_summary = _generate_summary_text(existing_summary_text, messages_to_cover)
    except Exception as exc:
        logger.error("Summarisation failed for conversation %s: %s", conversation_id, exc)
        return

    try:
        save_summary(
            conversation_id=conversation_id,
            summary_text=new_summary,
            summarized_up_to_message_id=last_covered_msg["message_id"],
            metadata={"messages_covered": len(messages_to_cover)},
            db_path=_db,
        )
    except Exception as exc:
        logger.error("Failed to persist summary for %s: %s", conversation_id, exc)


def build_context_block(
    conversation_id: str,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    from chat_store import get_all_messages_raw, get_latest_summary

    _db = db_path or CHAT_DB_PATH
    latest_summary = get_latest_summary(conversation_id, _db)
    summary_text = latest_summary["summary_text"] if latest_summary else ""

    all_msgs = get_all_messages_raw(conversation_id, _db)
    recent_msgs = all_msgs[-recent_window:] if len(all_msgs) > recent_window else all_msgs

    lc_messages = []
    if summary_text:
        lc_messages.append(
            SystemMessage(
                content=(
                    "The following is a summary of earlier conversation history. "
                    "Use it for context but always verify financial facts via tools:\n\n"
                    + summary_text
                )
            )
        )
    lc_messages.extend(_messages_to_lc(recent_msgs))

    return {
        "summary": summary_text,
        "recent_messages": recent_msgs,
        "lc_messages": lc_messages,
    }
