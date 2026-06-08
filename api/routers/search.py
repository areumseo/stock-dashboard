import os
import time
import hashlib
import anthropic
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

SYSTEM_PROMPT = """You are a stock information analyst. Search the web for the latest data, then respond with ONLY a raw JSON object.

CRITICAL: Your response must ALWAYS be valid JSON only — no markdown, no code blocks, no explanations, no prose. Even if data is incomplete, output JSON with "N/A" for missing values. Never apologize or explain in text.

Schema: {"items":[{"name":"string","code":"string","summary":"2 sentences: recent news + investment point","metrics":[{"label":"string","value":"string","positive":true|false|null}],"badge":"string","badgeType":"up|new|lev|down"}]}

Rules: metrics≤3, use the most recent data available."""

SYSTEM_PROMPT_QUICK = SYSTEM_PROMPT + " Return only the TOP 3 most important items. items≤3."
SYSTEM_PROMPT_FULL  = SYSTEM_PROMPT + " items≤10."

# ── 서버 사이드 캐시 (30분 TTL) ────────────────────────────────────────────────
_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, full_response)
CACHE_TTL = 30 * 60  # 30분


def _cache_key(prompt: str, lang: str) -> str:
    raw = f"{prompt}|{lang}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str) -> str | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None


def _set_cache(key: str, data: str) -> None:
    _cache[key] = (time.time(), data)


class SearchRequest(BaseModel):
    prompt: str
    lang: str = "ko"          # "ko" | "en"
    use_websearch: bool = True
    quick: bool = False        # True → 3개 카드 빠르게 (웹 검색 1회)


@router.post("")
def search(req: SearchRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    key = _cache_key(req.prompt, req.lang)

    # ── 캐시 히트: 즉시 반환 ────────────────────────────────────────────────
    cached = _get_cached(key)
    if cached:
        return StreamingResponse(iter([cached]), media_type="text/plain")

    # ── 캐시 미스: Claude 호출 ───────────────────────────────────────────────
    lang_instruction = (
        "Write all text fields (name, summary, badge, label) in Korean."
        if req.lang == "ko"
        else "Write all text fields in English."
    )
    base_prompt = SYSTEM_PROMPT_QUICK if req.quick else SYSTEM_PROMPT_FULL
    system = base_prompt + f" {lang_instruction}"

    max_tokens  = 600  if req.quick else 2000
    web_uses    = 1    if req.quick else 2

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": req.prompt}],
    )
    kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": web_uses}]

    def generate():
        collected = []
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    collected.append(text)
                    yield text
            # quick 요청은 캐시 저장 안 함 (full 결과만 캐시)
            if not req.quick:
                _set_cache(key, "".join(collected))
        except anthropic.RateLimitError:
            yield '{"error":"rate_limit"}'
        except Exception as e:
            yield f'{{"error":"{str(e)}"}}'

    return StreamingResponse(generate(), media_type="text/plain")
