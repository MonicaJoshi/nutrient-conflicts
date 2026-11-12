# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Ingredient string tokenizer.

Implements the project rules for deriving an item list from the USDA
``branded_food.ingredients`` text:

a. drop the boiler plate prefixes (``INGREDIENTS:``, ``MADE FROM:``,
   ``CONTAINS LESS THAN 2% OF:``, ``CONCENTRATED JUICES OF`` and the other
   variants configured in ``application.properties``);
b. split on the configured separators (comma and semicolon by default);
c. when an item carries a sub list in parenthesis or brackets, keep the sub
   items and drop the parent item.

Percentage-only groups such as ``(18%)`` are quantities, and functional labels
such as ``(PRESERVATIVES)`` are not ingredients; both are removed while the
parent item is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config

_WHITESPACE = re.compile(r"\s+")
_NUMERIC_ONLY = re.compile(r"^[\d.,%/+\-\s]*$")
_INLINE_PERCENT = re.compile(r"[\(\[]\s*\d+(?:[.,]\d+)?\s*%\s*[\)\]]")


def _prefix_pattern(prefix: str) -> re.Pattern[str]:
    """Compile a boiler-plate prefix, tolerating ``INGREDIENTS :`` and ``INGREDIENTS -``."""
    if not prefix:
        return re.compile(r"(?!x)x")
    if prefix.endswith(":"):
        body = prefix[:-1].rstrip()
        return re.compile(r"\b" + re.escape(body) + r"\s*:?", re.IGNORECASE)
    return re.compile(r"\b" + re.escape(prefix), re.IGNORECASE)


@dataclass(frozen=True)
class IngredientPhrase:
    """A single item derived from an ingredient string."""

    text: str
    index: int
    parent: str | None = None

    def as_dict(self) -> dict:
        return {"text": self.text, "index": self.index, "parent": self.parent}


@dataclass
class _Node:
    text: str = ""
    children: list["_Node"] = field(default_factory=list)


class IngredientParser:
    """Turn one ingredient string into an ordered list of item phrases."""

    def __init__(self, config: Config) -> None:
        self.ignore_prefixes = [item.upper() for item in config.get_list("parse.ignore.prefixes")]
        # USDA text writes the same label as "INGREDIENTS:", "INGREDIENTS :" and
        # occasionally "INGREDIENTS -", so the match tolerates the separator.
        self._prefix_patterns = [_prefix_pattern(item) for item in self.ignore_prefixes if item]
        self.separators = set(config.get("parse.separators", ",;"))
        self.group_open = set(config.get("parse.group.open", "(["))
        self.group_close = set(config.get("parse.group.close", ")]"))
        self.qualifiers = {item.upper() for item in config.get_list("parse.qualifier.tokens")}
        self.strip_chars = config.get("parse.strip.chars", '.;"*')
        self.max_phrases = config.get_int("parse.max.phrases.per.food", 400)
        self.max_phrase_length = config.get_int("parse.max.phrase.length", 80)
        self.drop_numeric_groups = config.get_bool("parse.drop.numeric.groups", True)

    # -- public API ------------------------------------------------------
    def parse(self, raw: str | None) -> list[IngredientPhrase]:
        if not raw or not raw.strip():
            return []
        text = self._prepare(raw)
        nodes = self._build_tree(text)
        collected: list[_Node] = []
        self._flatten(nodes, collected)
        return self._normalise(collected)

    # -- pipeline --------------------------------------------------------
    def _prepare(self, raw: str) -> str:
        text = raw.upper().replace("\n", " ").replace("\r", " ")
        for pattern in self._prefix_patterns:
            text = pattern.sub(" ", text)
        if self.drop_numeric_groups:
            text = _INLINE_PERCENT.sub(" ", text)
        return text

    def _build_tree(self, text: str) -> list[_Node]:
        root = _Node()
        stack: list[_Node] = [root]
        buffer: list[str] = []

        def flush() -> None:
            token = "".join(buffer).strip()
            buffer.clear()
            if token:
                stack[-1].children.append(_Node(token))

        for char in text:
            if char in self.group_open:
                name = "".join(buffer).strip()
                buffer.clear()
                group = _Node(name)
                stack[-1].children.append(group)
                stack.append(group)
            elif char in self.group_close:
                flush()
                if len(stack) > 1:
                    stack.pop()
            elif char in self.separators:
                flush()
            else:
                buffer.append(char)
        flush()
        # unbalanced opening groups: fold their contents back into the parent
        while len(stack) > 1:
            group = stack.pop()
            stack[-1].children.extend(group.children)
            if group.text:
                stack[-1].children.append(_Node(group.text))
        return root.children

    def _flatten(self, nodes: list[_Node], out: list[_Node]) -> None:
        """Rule (c): a node with sub items is replaced by its sub items."""
        for node in nodes:
            if node.children:
                before = len(out)
                self._flatten(node.children, out)
                if len(out) == before and self._keep(node.text):
                    out.append(_Node(node.text))
            elif self._keep(node.text):
                out.append(node)

    def _normalise(self, nodes: list[_Node]) -> list[IngredientPhrase]:
        phrases: list[IngredientPhrase] = []
        seen: set[str] = set()
        for node in nodes:
            text = self._clean(node.text)
            if not text or text in seen:
                continue
            seen.add(text)
            phrases.append(IngredientPhrase(text=text, index=len(phrases)))
            if len(phrases) >= self.max_phrases:
                break
        return phrases

    # -- helpers ---------------------------------------------------------
    def _keep(self, token: str) -> bool:
        text = self._clean(token)
        if not text or len(text) > self.max_phrase_length:
            return False
        if text in self.qualifiers:
            return False
        if _NUMERIC_ONLY.match(text):
            return False
        if not any(char.isalpha() for char in text):
            return False
        return True

    def _clean(self, token: str) -> str:
        text = token.strip().strip(self.strip_chars).strip()
        text = _WHITESPACE.sub(" ", text)
        if "(" in text or ")" in text or "[" in text or "]" in text:
            text = re.sub(r"[\(\)\[\]]", " ", text)
            text = _WHITESPACE.sub(" ", text).strip()
        return text.strip(self.strip_chars).strip()
