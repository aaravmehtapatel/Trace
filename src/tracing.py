"""PRISM tracing (BlockConvey's AI observability) for the Claude calls.

Off unless PRISMTRACE_API_KEY is set (census/.env) and prismtrace-sdk is installed. It
never raises: a tracing failure must not reach the pipeline. The SDK sends on its own
background threads, so a call costs the pipeline nothing but building the payload.
"""
import os
import time
import uuid

PROJECT_ID = "c84720c1-f793-4480-b4f1-30f78f71a1c4"
HOST = "https://api.prism.blockconvey.com"
AGENT_ID = "census-domain-proposer"
AGENT_NAME = "Census domain proposer"

_tracer = None
_started = False


def _pt():
    """The PRISMtrace client, built on first use; None when tracing is off."""
    global _tracer, _started
    if not _started:
        _started = True
        key = os.environ.get("PRISMTRACE_API_KEY")
        if key:
            try:
                from prismtrace import PRISMtrace
                # 5s, not the SDK's 30: its sends are non-daemon threads, so a PRISM that
                # hangs would hold the CLI open at exit for the whole timeout.
                _tracer = PRISMtrace(api_key=key, host=os.environ.get("PRISMTRACE_HOST") or HOST,
                                     project_id=os.environ.get("PRISMTRACE_PROJECT_ID") or PROJECT_ID, timeout=5)
            except Exception:
                _tracer = None
    return _tracer


def new_session(kind: str) -> str:
    """One id per run, so every batch in that run lands in one PRISM session."""
    return f"census-{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def llm(session_id: str, model: str, messages: list[dict], started: float, msg=None, error=None) -> None:
    """Record one Messages call: its response `msg`, or the `error` it raised."""
    try:
        pt = _pt()
        if pt is None:
            return
        if msg is not None:
            output = "".join(b.text for b in msg.content if b.type == "text")
            usage = (msg.usage.input_tokens, msg.usage.output_tokens)
            meta = {"stop_reason": msg.stop_reason}
            model = msg.model or model
        else:
            output, usage = "", (0, 0)
            meta = {"status": "error", "error": f"{type(error).__name__}: {error}"[:500]}
        pt.trace_llm(model=model, input_messages=messages, output=output,
                     latency_ms=int((time.perf_counter() - started) * 1000),
                     token_count_input=usage[0], token_count_output=usage[1],
                     agent_id=AGENT_ID, agent_name=AGENT_NAME, session_id=session_id, metadata=meta)
    except Exception:
        pass
