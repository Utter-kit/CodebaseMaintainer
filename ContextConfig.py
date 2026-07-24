@dataclass
class ContextConfig:
    """上下文构建配置

    Attributes:
        max_tokens: 最大 token 数量
        reserve_ratio: 为系统指令预留的比例(0.0-1.0)
        min_relevance: 最低相关性阈值
        enable_compression: 是否启用压缩
        recency_weight: 新近性权重(0.0-1.0)
        relevance_weight: 相关性权重(0.0-1.0)
    """
    max_tokens: int = 3000
    reserve_ratio: float = 0.2
    min_relevance: float = 0.1
    enable_compression: bool = True
    recency_weight: float = 0.3
    relevance_weight: float = 0.7

    def __post_init__(self):
        """验证配置参数"""
        assert 0.0 <= self.reserve_ratio <= 1.0, "reserve_ratio 必须在 [0, 1] 范围内"
        assert 0.0 <= self.min_relevance <= 1.0, "min_relevance 必须在 [0, 1] 范围内"
        assert abs(self.recency_weight + self.relevance_weight - 1.0) < 1e-6, \
            "recency_weight + relevance_weight 必须等于 1.0"

    def _gather(
        self,
        user_query: str,
        conversation_history: Optional[List[Any]] = None,
        system_instructions: Optional[str] = None,
        custom_packets: Optional[List[ContextPacket]] = None
    ) -> List[ContextPacket]:
        packets = []

        # 1. 添加系统指令(最高优先级,不参与评分)
        if system_instructions:
            packets.append(ContextPacket(
                content=system_instructions,
                timestamp=datetime.now(),
                token_count=self._count_tokens(system_instructions),
                relevance_score=1.0,  # 系统指令始终保留
                metadata={"type": "system_instruction", "priority": "high"}
            ))

        # 2. 从记忆系统检索相关记忆
        if self.memory_tool:
            try:
                memory_results = self.memory_tool.run({
                    "action": "search",
                    "query": user_query,
                    "limit": 10,
                    "min_importance": 0.3
                })
                # 解析记忆结果并转换为 ContextPacket
                memory_packets = self._parse_memory_results(memory_results, user_query)
                packets.extend(memory_packets)
            except Exception as e:
                print(f"[WARNING] 记忆检索失败: {e}")

        # 3. 从 RAG 系统检索相关知识
        if self.rag_tool:
            try:
                rag_results = self.rag_tool.run({
                    "action": "search",
                    "query": user_query,
                    "limit": 5,
                    "min_score": 0.3
                })
                # 解析 RAG 结果并转换为 ContextPacket
                rag_packets = self._parse_rag_results(rag_results, user_query)
                packets.extend(rag_packets)
            except Exception as e:
                print(f"[WARNING] RAG 检索失败: {e}")

        # 4. 添加对话历史(仅保留最近的 N 条)
        if conversation_history:
            recent_history = conversation_history[-5:]  # 默认保留最近 5 条
            for msg in recent_history:
                packets.append(ContextPacket(
                    content=f"{msg.role}: {msg.content}",
                    timestamp=msg.timestamp if hasattr(msg, 'timestamp') else datetime.now(),
                    token_count=self._count_tokens(msg.content),
                    relevance_score=0.6,  # 历史消息的基础相关性
                    metadata={"type": "conversation_history", "role": msg.role}
                ))

        # 5. 添加自定义信息包
        if custom_packets:
            packets.extend(custom_packets)

        print(f"[ContextBuilder] 汇集了 {len(packets)} 个候选信息包")
        return packets