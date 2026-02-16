from openai_client import client, MODEL


def stream_chat(message: str, session: dict, system_prompt: str):
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    messages = [{"role": "system", "content": system_prompt}] + history
    parts = []

    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            parts.append(delta)
            yield delta
    except Exception:
        error_text = (
            "I apologize for the technical issue. Please try again or contact support."
        )
        parts = [error_text]
        yield error_text

    reply = "".join(parts)
    history.append({"role": "assistant", "content": reply})
