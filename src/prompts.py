"""
RLM prompt templates.

Implements: Spec §3.2 Root Prompt Structure
"""

from __future__ import annotations

from .types import SessionContext


def build_rlm_system_prompt(context: SessionContext, query: str) -> str:
    """
    Build the RLM system prompt.

    Implements: Spec §3.2 Root Prompt Structure

    Args:
        context: Session context with externalized data
        query: User's query

    Returns:
        System prompt for RLM mode
    """
    n_messages = len(context.messages)
    message_tokens = sum(len(m.get("content", "")) // 4 for m in context.messages)

    n_files = len(context.files)
    file_tokens = sum(len(content) // 4 for content in context.files.values())

    n_outputs = len(context.tool_outputs)
    output_tokens = sum(len(str(o.get("content", ""))) // 4 for o in context.tool_outputs)

    return f"""You are Claude Code operating in RLM (Recursive Language Model) mode. Your conversation context is stored in variables in a Python REPL environment.

## Available Variables
- `conversation`: {n_messages} messages, ~{message_tokens} tokens
- `files`: {n_files} files cached (~{file_tokens} tokens)
- `tool_outputs`: {n_outputs} recent outputs (~{output_tokens} tokens)
- `working_memory`: dict for storing intermediate results

## REPL Actions
To interact with context, output Python code in ```python blocks. The code will be executed and results returned.

1. **Peek**: View portions of context
   ```python
   peek(conversation, 0, 5)  # First 5 messages
   peek(files['main.py'], 0, 50)  # First 50 lines
   ```

2. **Search**: Find patterns in context
   ```python
   search(files, 'def ')  # Find all function definitions
   search(conversation, 'error', regex=True)  # Regex search
   ```

3. **Summarize**: Get summaries of large content
   ```python
   summarize(files['large_file.py'], max_tokens=500)
   ```

4. **Query**: Ask sub-questions about context chunks
   ```python
   result = recursive_query("What does this function do?", files['main.py'])
   ```

5. **Store**: Save results for later use
   ```python
   working_memory['analysis'] = result
   ```
{_web_section()}
## Current Query
{query}

## Rules
- DON'T request full context dumps—use programmatic access via REPL
- Partition large contexts before analysis
- Use recursive_query for semantic understanding of large chunks
- Store intermediate results in working_memory
- When you have the answer, output: FINAL: <your answer>
- If the answer is in a variable: FINAL_VAR: <variable_name>

## Response Format
Think through the problem, then either:
1. Output ```python code to execute in REPL
2. Output FINAL: <answer> when you have the complete answer"""


def _web_section() -> str:
    """REPL docs for the browser-use lane (empty when RLM_BROWSER=0)."""
    import os

    if os.environ.get("RLM_BROWSER", "1").strip().lower() in {"0", "false", "no", "off"}:
        return ""
    return """
6. **Web**: Pull live pages into context (parallel; text lands in `web[url]`)
   ```python
   fetch_pages(["https://a.com/x", "https://b.com/y"])   # HTTP, auto-falls back to headless Chromium
   fetch_pages(urls, render=True)                       # force JS rendering
   crawl("https://docs.x.com", max_pages=30, match="/api/")
   browse(url, steps=[{"fill": ["#q", "rlm"]}, {"press": ["#q", "Enter"]}, {"wait": ".results"}])
   answers = llm_batch([("Key claims?", web[u]) for u in web])  # recurse over pages
   ```
   Pages that need the user's login are listed in `working_memory['chrome_queue']`;
   leave them for the agent's own browser (they come back via `ingest_page`).
"""


def build_recursive_prompt(query: str, context_chunk: str) -> str:
    """
    Build prompt for recursive sub-calls.

    Implements: Spec §3.3 Recursive Call Protocol

    Args:
        query: Sub-query to answer
        context_chunk: Context for this query

    Returns:
        Prompt for recursive call
    """
    return f"""Answer this question based on the provided context:

## Question
{query}

## Context
{context_chunk}

## Instructions
- Answer based ONLY on the provided context
- Be concise and specific
- If the context doesn't contain the answer, say so
- Don't make assumptions beyond what's in the context"""


def build_summarization_prompt(content: str, max_tokens: int) -> str:
    """
    Build prompt for summarization sub-calls.

    Args:
        content: Content to summarize
        max_tokens: Target length

    Returns:
        Prompt for summarization
    """
    return f"""Summarize the following content in approximately {max_tokens} tokens:

{content}

## Instructions
- Preserve key information and structure
- Be concise but complete
- Maintain technical accuracy
- Focus on the most important points"""


__all__ = [
    "build_recursive_prompt",
    "build_rlm_system_prompt",
    "build_summarization_prompt",
]
