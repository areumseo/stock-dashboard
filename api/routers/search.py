import os
import time
import hashlib
import secrets
import json
import requests
import anthropic
import yfinance as yf
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://m.stock.naver.com/",
}

# Claude generates name/code/summary/badge only — no price metrics (yfinance handles those)
SYSTEM_PROMPT = """You are a stock data API. You MUST respond with raw JSON only — absolutely no prose, no markdown, no backticks, no explanations, no apologies.

Your FIRST character must be { and your LAST character must be }.

Schema: {"items":[{"name":"string","code":"string","summary":"1 sentence: recent news + key investment point","badge":"string","badgeType":"up|new|lev|down"}]}

Rules: items≤20. Use the exact ticker code (e.g. 005930 for Samsung, AAPL for Apple). Do NOT include metrics — they are fetched separately."""

# ── 서버 사이드 캐시 (30분 TTL) ────────────────────────────────────────────────
_cache: dict[str, tuple[float, str]] = {}
CACHE_TTL = 30 * 60


def _cache_key(prompt: str, lang: str) -> str:
    return hashlib.md5(f"{prompt}|{lang}".encode()).hexdigest()


def _get_cached(key: str) -> str | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None


def _set_cache(key: str, data: str) -> None:
    _cache[key] = (time.time(), data)


# ── yfinance 실시간 메트릭 조회 ───────────────────────────────────────────────

def _fmt_usd_cap(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    return f"${value / 1e6:.0f}M"


def _fetch_naver_metrics(code: str) -> list[dict]:
    """한국 주식: 네이버 증권 모바일 API (링크 클릭 시 보이는 화면과 동일 수치)."""
    try:
        basic = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers=_NAVER_HEADERS, timeout=4,
        ).json()
    except Exception:
        return []

    metrics = []

    close = basic.get("closePrice")
    ratio = basic.get("fluctuationsRatio")
    direction = (basic.get("compareToPreviousPrice") or {}).get("name")  # RISING/FALLING
    up = direction == "RISING"

    if close:
        metrics.append({"label": "현재가", "value": f"₩{close}", "positive": None})

    if ratio not in (None, ""):
        sign = "+" if not str(ratio).startswith("-") else ""
        metrics.append({"label": "등락률", "value": f"{sign}{ratio}%", "positive": up})

    # 시가총액은 integration 엔드포인트에서
    try:
        integ = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/integration",
            headers=_NAVER_HEADERS, timeout=4,
        ).json()
        for info in integ.get("totalInfos", []):
            if info.get("code") == "marketValue":
                metrics.append(
                    {"label": "시가총액", "value": info.get("value", "N/A"), "positive": None}
                )
                break
    except Exception:
        pass

    return metrics


def _fetch_yfinance_metrics(code: str) -> list[dict]:
    """미국 주식: yfinance."""
    try:
        fi = yf.Ticker(code).fast_info
        metrics = []
        price = fi.last_price
        prev_close = fi.previous_close
        market_cap = fi.market_cap

        if price:
            metrics.append({"label": "Price", "value": f"${price:,.2f}", "positive": None})

        if price and prev_close and prev_close > 0:
            chg = (price - prev_close) / prev_close * 100
            sign = "+" if chg >= 0 else ""
            metrics.append({"label": "Change", "value": f"{sign}{chg:.2f}%", "positive": chg >= 0})

        if market_cap:
            metrics.append({"label": "Mkt Cap", "value": _fmt_usd_cap(market_cap), "positive": None})

        return metrics
    except Exception:
        return []


def _fetch_real_metrics(code: str, is_korean: bool) -> list[dict]:
    if not code:
        return []
    return _fetch_naver_metrics(code) if is_korean else _fetch_yfinance_metrics(code)


# ── 스트리밍 버퍼에서 완성된 item 추출 (Flutter의 brace-counter와 동일 로직) ──

def _matching_brace(s: str, start: int) -> int:
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        if esc:
            esc = False
            continue
        c = s[i]
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_next_item(s: str) -> Optional[tuple[dict, str]]:
    search_from = 0
    while True:
        name_idx = s.find('"name"', search_from)
        if name_idx == -1:
            return None
        brace_start = -1
        for i in range(name_idx - 1, -1, -1):
            c = s[i]
            if c == "{":
                brace_start = i
                break
            if c in ("[", "]", "}"):
                break
        if brace_start == -1:
            search_from = name_idx + 1
            continue
        brace_end = _matching_brace(s, brace_start)
        if brace_end == -1:
            return None
        candidate = s[brace_start : brace_end + 1]
        try:
            data = json.loads(candidate)
            if "name" in data and "summary" in data:
                return data, s[brace_end + 1 :]
        except json.JSONDecodeError:
            pass
        search_from = name_idx + 1


# ── API ───────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    prompt: str
    lang: str = "ko"
    use_websearch: bool = True
    quick: bool = False


def _check_api_key(x_api_key: Optional[str]) -> None:
    app_key = os.environ.get("APP_API_KEY")
    if not app_key:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, app_key):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


@router.post("")
def search(req: SearchRequest, x_api_key: Optional[str] = Header(default=None)):
    _check_api_key(x_api_key)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    key = _cache_key(req.prompt, req.lang)

    cached = _get_cached(key)
    if cached:
        return StreamingResponse(iter([cached]), media_type="text/plain")

    lang_instruction = (
        "Write name, summary, badge in Korean."
        if req.lang == "ko"
        else "Write name, summary, badge in English."
    )
    system = SYSTEM_PROMPT + f" {lang_instruction}"

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": req.prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
    )

    is_korean = req.lang == "ko"

    def generate():
        buffer = ""
        collected = []
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    buffer += text
                    while True:
                        result = _extract_next_item(buffer)
                        if result is None:
                            break
                        item, buffer = result
                        # 실시간 가격으로 metrics 교체
                        item["metrics"] = _fetch_real_metrics(item.get("code", ""), is_korean)
                        enriched = json.dumps(item, ensure_ascii=False)
                        collected.append(enriched)
                        yield enriched
            _set_cache(key, "".join(collected))
        except anthropic.RateLimitError:
            yield '{"error":"rate_limit"}'
        except Exception as e:
            yield f'{{"error":"{str(e)}"}}'

    return StreamingResponse(generate(), media_type="text/plain")
