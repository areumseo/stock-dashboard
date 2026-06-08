import os
import anthropic
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

SYSTEM_PROMPT = """You are a stock information analyst. Search the web for the latest data, then respond with ONLY newline-delimited JSON (NDJSON).

CRITICAL: Output each stock as a SEPARATE JSON object on its own line. Do NOT wrap in an array. No markdown, no code blocks, no explanations. Each line must be independently valid JSON.

Line schema: {"name":"string","code":"string","summary":"2 sentences: recent news + investment point","metrics":[{"label":"string","value":"string","positive":true|false|null}],"badge":"string","badgeType":"up|new|lev|down"}

Rules: up to 20 lines (one stock per line), metrics≤3, use the most recent data available."""


class SearchRequest(BaseModel):
    prompt: str
    lang: str = "ko"          # "ko" | "en"
    use_websearch: bool = True


@router.post("")
def search(req: SearchRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    lang_instruction = (
        "Write all text fields (name, summary, badge, label) in Korean."
        if req.lang == "ko"
        else "Write all text fields in English."
    )
    system = SYSTEM_PROMPT + f" {lang_instruction}"

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model="claude-sonnet-4-5",
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": req.prompt}],
    )
    if req.use_websearch:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]

    def generate():
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
        except anthropic.RateLimitError:
            yield '{"error":"rate_limit"}'
        except Exception as e:
            yield f'{{"error":"{str(e)}"}}'

    return StreamingResponse(generate(), media_type="text/plain")
