# Part C: nine offline experiments (no LLM, no API calls). Run: python claims_experiments.py
import operator, traceback
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode
from langgraph.errors import GraphRecursionError

class Plain(TypedDict):  messages: list
class Reduced(TypedDict): messages: Annotated[list, add_messages]
def line(n, v): print(f"\n--- Claim {n}: verdict {v} ---")

line(1, "FALSE")                                    # reducer decides replace vs append
for S in (Plain, Reduced):
    g = StateGraph(S); g.add_node("n", lambda s: {"messages": [AIMessage(content="new")]})
    g.add_edge(START, "n"); g.add_edge("n", END)
    out = g.compile().invoke({"messages": [HumanMessage(content="old")]})
    print(S.__name__, "->", [m.content for m in out["messages"]])

line(2, "FALSE")                                    # bind_tools only describes tools; nothing runs
calls = []
@tool
def ping(x: str) -> str:
    """ping"""; calls.append(x); return "pong"
class Fake:
    def bind_tools(self, t): self.t = t; return self
    def invoke(self, m): return AIMessage(content="", tool_calls=[{"name": "ping", "args": {"x": "1"}, "id": "a"}])
r = Fake().bind_tools([ping]).invoke([HumanMessage(content="hi")])
print("model asked for:", r.tool_calls[0]["name"], "| tool actually executed:", calls)

line(3, "TRUE")                                     # router returns END
g = StateGraph(Reduced); g.add_node("a", lambda s: {"messages": [AIMessage(content="a ran")]})
g.add_node("b", lambda s: {"messages": [AIMessage(content="b ran")]})
g.add_edge(START, "a"); g.add_conditional_edges("a", lambda s: END, ["b", END])
print([m.content for m in g.compile().invoke({"messages": []})["messages"]])

line(4, "FALSE")                                    # MemorySaver lives in RAM only
def make(cp):
    g = StateGraph(Reduced); g.add_node("a", lambda s: {"messages": [AIMessage(content="x")]})
    g.add_edge(START, "a"); g.add_edge("a", END); return g.compile(checkpointer=cp)
cfg = {"configurable": {"thread_id": "t1"}}
cp1 = MemorySaver(); make(cp1).invoke({"messages": [HumanMessage(content="hi")]}, cfg)
print("same process:", len(make(cp1).get_state(cfg).values.get("messages", [])), "messages")
print("after 'restart' (new MemorySaver):", len(make(MemorySaver()).get_state(cfg).values.get("messages", [])), "messages")

line(5, "FALSE")                                    # interrupt_before pauses BEFORE the node
g = StateGraph(Reduced); g.add_node("x", lambda s: (print("   node x is running"), {"messages": [AIMessage(content="x done")]})[1])
g.add_edge(START, "x"); g.add_edge("x", END)
app = g.compile(checkpointer=MemorySaver(), interrupt_before=["x"]); c = {"configurable": {"thread_id": "t"}}
app.invoke({"messages": [HumanMessage(content="go")]}, c)
print("paused, next node to run:", app.get_state(c).next, "| x has run yet?", any(m.content == "x done" for m in app.get_state(c).values["messages"]))

line(6, "FALSE")                                    # thread_id separates histories
cp = MemorySaver(); app = make(cp)
app.invoke({"messages": [HumanMessage(content="from A")]}, {"configurable": {"thread_id": "A"}})
app.invoke({"messages": [HumanMessage(content="from B")]}, {"configurable": {"thread_id": "B"}})
for t in "AB": print("thread", t, [m.content for m in app.get_state({"configurable": {"thread_id": t}}).values["messages"]])

line(7, "FALSE")                                    # no such compile() argument
try: StateGraph(Reduced).compile(checkpointer=MemorySaver(), auto_approve=True)
except TypeError as e: print("TypeError:", e)

line(8, "TRUE")                                     # counts steps (node executions), no tools needed
g = StateGraph(Reduced); g.add_node("a", lambda s: {"messages": []}); g.add_node("b", lambda s: {"messages": []})
g.add_edge(START, "a"); g.add_edge("a", "b"); g.add_edge("b", "a")
try: g.compile().invoke({"messages": []}, {"recursion_limit": 5})
except GraphRecursionError as e: print("GraphRecursionError raised with zero tool calls:", str(e)[:60])

line(9, "TRUE")                                     # one ToolNode, two tool calls in one message
@tool
def add(a: int, b: int) -> int:
    """add"""; return a + b
msg = AIMessage(content="", tool_calls=[{"name": "add", "args": {"a": 1, "b": 2}, "id": "1"}, {"name": "add", "args": {"a": 10, "b": 20}, "id": "2"}])
g = StateGraph(Reduced); g.add_node("tools", ToolNode([add])); g.add_edge(START, "tools"); g.add_edge("tools", END)
print("tool results:", [m.content for m in g.compile().invoke({"messages": [msg]})["messages"] if m.type == "tool"])
