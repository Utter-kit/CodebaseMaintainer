from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime

@dataclass
class ContextPacket:
    """候选信息包

    Attributes:
        content: 信息内容
        timestamp: 时间戳
        token_count: Token 数量
        relevance_score: 相关性分数(0.0-1.0)
        metadata: 可选的元数据
    """
    content: str
    timestamp: datetime
    token_count: int
    relevance_score: float = 0.5
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}
        # 确保相关性分数在有效范围内
        self.relevance_score = max(0.0, min(1.0, self.relevance_score))
        
    def _select(
        self,
        packets: List[ContextPacket],
        user_query: str,
        available_tokens: int
    ) -> List[ContextPacket]:
        # 1. 分离系统指令和其他信息
        system_packets = [p for p in packets if p.metadata.get("type") == "system_instruction"]
        other_packets = [p for p in packets if p.metadata.get("type") != "system_instruction"]

        # 2. 计算系统指令占用的 token
        system_tokens = sum(p.token_count for p in system_packets)
        remaining_tokens = available_tokens - system_tokens

        if remaining_tokens <= 0:
            print("[WARNING] 系统指令已占满所有 token 预算")
            return system_packets

        # 3. 为其他信息计算综合分数
        scored_packets = []
        for packet in other_packets:
            # 计算相关性分数(如果尚未计算)
            if packet.relevance_score == 0.5:  # 默认值,需要重新计算
                relevance = self._calculate_relevance(packet.content, user_query)
                packet.relevance_score = relevance

            # 计算新近性分数
            recency = self._calculate_recency(packet.timestamp)

            # 综合分数 = 相关性权重 × 相关性 + 新近性权重 × 新近性
            combined_score = (
                self.relevance_weight * packet.relevance_score +
                self.recency_weight * recency
            )

            # 过滤低于最小相关性阈值的信息
            if packet.relevance_score >= self.min_relevance:
                scored_packets.append((combined_score, packet))

        # 4. 按分数降序排序
        scored_packets.sort(key=lambda x: x[0], reverse=True)

        # 5. 贪心选择:按分数从高到低填充,直到达到 token 上限
        selected = system_packets.copy()
        current_tokens = system_tokens

        for score, packet in scored_packets:
            if current_tokens + packet.token_count <= available_tokens:
                selected.append(packet)
                current_tokens += packet.token_count
            else:
                # Token 预算已满,停止选择
                break

        print(f"[ContextBuilder] 选择了 {len(selected)} 个信息包,共 {current_tokens} tokens")
        return selected
