import os
import time
import hashlib
import anthropic
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

SYSTEM_PROMPT = """You are a stock data API. You MUST respond with raw JSON only — absolutely no prose, no markdown, no backticks, no explanations, no apologies.

Your FIRST character must be { and your LAST character must be }.

If data is unavailable or uncertain, use approximate values or "N/A" — but NEVER write explanatory sentences. If you cannot find exact real-time data, use the most recent available data you found and fill in what you can.

Schema: {"items":[{"name":"string","code":"string","summary":"1 sentence: recent news + key investment point","metrics":[{"label":"string","value":"string","positive":true|false|null}],"badge":"string","badgeType":"up|new|lev|down"}]}

Rules: items≤20, metrics≤3."""

SYSTEM_PROMPT_QUICK = SYSTEM_PROMPT  # kept for backward compat, quick mode removed
SYSTEM_PROMPT_FULL  = SYSTEM_PROMPT

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

    max_tokens = 800 if req.quick else 2000

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": req.prompt}],
    )
    # quick=True: 웹 검색 없이 학습 데이터로 즉시 응답 (2-3초)
    # quick=False: 웹 검색 2회로 최신 실제 데이터 (10-20초)
    if not req.quick:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]

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
