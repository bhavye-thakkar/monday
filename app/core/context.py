"""What the chat model is told: who it is, the live state, what Monday remembers that bears on the question and, right
after a browser request, the page Monday's Chrome is on. Nothing else is sent.

A finished or failed task is announced where it ends (Monday._finish: one reply with a spoken line); nothing here polls for it.
"""
import time

from . import memory


class ContextManager:
    def __init__(self, monday):
        self.monday = monday

    def get_system_prompt(self, question, running_tasks, components, jev_decision=None, compact=False):
        """The chat model's system prompt: the rules and the live state, plus only the context this question needs. compact: for a model on
        this PC (2026-10-24: llama3.2:1b answered "2+2?" with "Square." under the full one, "Four." without it): who it is, the time, the rule
        that chat can't act; no list of components."""
        if compact:
            base = (f"You are {self.monday.name}, the owner's assistant on their Windows PC. Local time: {time.strftime('%A %d %B %Y, %H:%M')}. "
                    "Answer briefly and directly. You can't act on the computer from this chat: never claim you did.")
        else:
            base = (f"You are {self.monday.name}, the owner's personal assistant running on their own Windows PC. "
                    f"Local time: {time.strftime('%A %d %B %Y, %H:%M')}. Be brief and direct.\n"
                    "In this chat you cannot run tools or act on the computer. Never claim you did something. "
                    "Monday itself routes requests to act on the computer, in a project or on the web to its own tools and agents, in "
                    "whatever words they come: you answer questions and talk; never tell the owner how to phrase a command.\n"
                    f"Live state from Monday Core (authoritative): running tasks: {running_tasks}. Components: {components}.")
        blocks = [base]
        mem = memory.context(question, self.monday._session_memories())
        if mem:
            blocks.append(mem)
        page = getattr(self.monday.chrome, "page", None) if (jev_decision or {}).get("tool") == "browser" else None
        if page is not None:  # the address only: reading the page is a tool's job (browser › read_page), not the prompt's
            blocks.append(f"Monday's Chrome is on: {page.url}")
        return "\n".join(blocks)
