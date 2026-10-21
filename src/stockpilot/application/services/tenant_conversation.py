"""Internal read-only RAG graph. Never composes the historical global workflow."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from stockpilot.domain.entities.tenant_knowledge import text


class TenantRagState(TypedDict, total=False):
    question: str
    answer: str | None


class TenantRagConversation:
    def __init__(self, conversations, retrieval):
        self.conversations, self.retrieval = conversations, retrieval

    def _graph(self, request, context, saver):
        async def answer(state):
            value = await self.retrieval.execute(request, context, state["question"])
            return {"answer": value}

        builder = StateGraph(TenantRagState)
        builder.add_node("answer", answer)
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        return builder.compile(checkpointer=saver)

    async def execute(self, request, context, conversation_id, question):
        text(question, 4000)
        saver = self.conversations.bind(request, context, conversation_id)
        with self.conversations.execution(request, context, conversation_id):
            return await self._graph(request, context, saver).ainvoke(
                {"question": question}, saver.config()
            )

    async def state(self, request, context, conversation_id):
        saver = self.conversations.bind(request, context, conversation_id)
        return await self._graph(request, context, saver).aget_state(saver.config())
