from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
import re
from typing import Any, Iterable


@dataclass
class ContextPacket:
    """A candidate piece of context for the long-running assistant."""

    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    relevance_score: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int | None = None

    def __post_init__(self) -> None:
        self.relevance_score = max(0.0, min(1.0, self.relevance_score))
        if self.token_count is None:
            self.token_count = estimate_tokens(self.content)


@dataclass
class ContextConfig:
    """Controls gathering, ranking, and compression for context packets."""

    max_tokens: int = 4000
    reserve_ratio: float = 0.15
    min_relevance: float = 0.1
    enable_compression: bool = True
    recency_weight: float = 0.3
    relevance_weight: float = 0.7
    history_limit: int = 8

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not 0.0 <= self.reserve_ratio <= 1.0:
            raise ValueError("reserve_ratio must be in [0, 1]")
        if not 0.0 <= self.min_relevance <= 1.0:
            raise ValueError("min_relevance must be in [0, 1]")
        if abs(self.recency_weight + self.relevance_weight - 1.0) > 1e-6:
            raise ValueError("recency_weight + relevance_weight must equal 1.0")


class ContextBuilder:
    """Gather, select, structure, and compress context for codebase work."""

    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    def build(
        self,
        user_query: str,
        conversation_history: Iterable[Any] | None = None,
        system_instructions: str | None = None,
        custom_packets: Iterable[ContextPacket] | None = None,
    ) -> str:
        packets = self._gather(
            user_query=user_query,
            conversation_history=conversation_history,
            system_instructions=system_instructions,
            custom_packets=custom_packets,
        )
        available_tokens = int(self.config.max_tokens * (1 - self.config.reserve_ratio))
        selected = self._select(packets, user_query, available_tokens)
        context = self._structure(selected, user_query)
        if self.config.enable_compression:
            context = self._compress(context, self.config.max_tokens)
        return context

    def _gather(
        self,
        user_query: str,
        conversation_history: Iterable[Any] | None = None,
        system_instructions: str | None = None,
        custom_packets: Iterable[ContextPacket] | None = None,
    ) -> list[ContextPacket]:
        packets: list[ContextPacket] = []

        if system_instructions:
            packets.append(
                ContextPacket(
                    content=system_instructions,
                    relevance_score=1.0,
                    metadata={"type": "system_instruction", "priority": "high"},
                )
            )

        history = list(conversation_history or [])[-self.config.history_limit :]
        for msg in history:
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", str(msg))
            timestamp = getattr(msg, "timestamp", datetime.now())
            packets.append(
                ContextPacket(
                    content=f"{role}: {content}",
                    timestamp=timestamp,
                    relevance_score=0.6,
                    metadata={"type": "conversation_history", "role": role},
                )
            )

        packets.extend(custom_packets or [])
        return packets

    def _select(
        self,
        packets: list[ContextPacket],
        user_query: str,
        available_tokens: int,
    ) -> list[ContextPacket]:
        system_packets = [
            packet
            for packet in packets
            if packet.metadata.get("type") == "system_instruction"
        ]
        other_packets = [
            packet
            for packet in packets
            if packet.metadata.get("type") != "system_instruction"
        ]
        system_tokens = sum(packet.token_count or 0 for packet in system_packets)
        remaining_tokens = max(0, available_tokens - system_tokens)

        scored_packets: list[tuple[float, ContextPacket]] = []
        for packet in other_packets:
            relevance = packet.relevance_score
            if relevance == 0.5:
                relevance = calculate_relevance(packet.content, user_query)
                packet.relevance_score = relevance

            if relevance < self.config.min_relevance:
                continue

            recency = calculate_recency(packet.timestamp)
            combined_score = (
                self.config.relevance_weight * relevance
                + self.config.recency_weight * recency
            )
            scored_packets.append((combined_score, packet))

        scored_packets.sort(key=lambda item: item[0], reverse=True)

        selected = list(system_packets)
        current_tokens = system_tokens
        for _, packet in scored_packets:
            packet_tokens = packet.token_count or 0
            if current_tokens + packet_tokens <= system_tokens + remaining_tokens:
                selected.append(packet)
                current_tokens += packet_tokens

        return selected

    def _structure(self, selected_packets: list[ContextPacket], user_query: str) -> str:
        sections: list[str] = []
        grouped: dict[str, list[str]] = {
            "system_instruction": [],
            "task_state": [],
            "code_structure": [],
            "code_analysis": [],
            "note": [],
            "conversation_history": [],
            "general": [],
        }

        for packet in selected_packets:
            packet_type = packet.metadata.get("type", "general")
            grouped.setdefault(packet_type, []).append(packet.content)

        if grouped["system_instruction"]:
            sections.append("[Role & Policies]\n" + "\n".join(grouped["system_instruction"]))

        sections.append(f"[Task]\n{user_query}")

        labels = {
            "task_state": "Task State",
            "code_structure": "Code Structure",
            "code_analysis": "Code Analysis",
            "note": "Persistent Notes",
            "conversation_history": "Recent Conversation",
            "general": "Additional Context",
        }
        for packet_type, label in labels.items():
            items = grouped.get(packet_type) or []
            if items:
                sections.append(f"[{label}]\n" + "\n---\n".join(items))

        sections.append(
            "[Output]\n"
            "请给出清晰、可执行的维护建议；涉及代码问题时请说明文件、证据和下一步。"
        )
        return "\n\n".join(sections)

    def _compress(self, context: str, max_tokens: int) -> str:
        if estimate_tokens(context) <= max_tokens:
            return context

        sections = context.split("\n\n")
        compressed_sections: list[str] = []
        current_tokens = 0
        for section in sections:
            section_tokens = estimate_tokens(section)
            if current_tokens + section_tokens <= max_tokens:
                compressed_sections.append(section)
                current_tokens += section_tokens
                continue

            remaining_tokens = max_tokens - current_tokens
            if remaining_tokens > 40:
                compressed_sections.append(truncate_text(section, remaining_tokens))
                compressed_sections.append("[... 内容已压缩 ...]")
            break

        return "\n\n".join(compressed_sections)


def calculate_relevance(content: str, query: str) -> float:
    content_words = set(_tokenize(content))
    query_words = set(_tokenize(query))
    if not query_words:
        return 0.0
    intersection = content_words & query_words
    union = content_words | query_words
    return len(intersection) / len(union) if union else 0.0


def calculate_recency(timestamp: datetime) -> float:
    age_hours = max(0.0, (datetime.now() - timestamp).total_seconds() / 3600)
    return max(0.1, min(1.0, math.exp(-0.1 * age_hours / 24)))


def estimate_tokens(text: str) -> int:
    chinese_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    words = len(re.findall(r"[A-Za-z0-9_./:-]+", text))
    punctuation = max(1, len(text) - chinese_chars - words) // 8
    return max(1, int(chinese_chars + words * 1.3 + punctuation))


def truncate_text(text: str, max_tokens: int) -> str:
    if estimate_tokens(text) <= max_tokens:
        return text
    ratio = max_tokens / max(estimate_tokens(text), 1)
    max_chars = max(1, int(len(text) * ratio))
    return text[:max_chars].rstrip()


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_./:-]+", text.lower())
    return [word for word in words if word.strip()]
