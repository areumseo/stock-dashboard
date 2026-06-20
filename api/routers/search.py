from __future__ import annotations

import os
import time
import hashlib
import secrets
import json
import queue
import threading
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

# ── 2단 캐시 (B′) ──────────────────────────────────────────────────────────────
# 비싼 Claude 결과(종목 리스트+요약)는 길게 캐시하고, 가격 수치는 매 요청마다 실시간으로
# 다시 붙인다. 덕분에 요약은 반나절 신선 + 수치는 항상 실시간 + 응답은 2~3초.
_list_cache: dict[str, tuple[float, list[dict]]] = {}  # key → (ts, bare items[no metrics])
LIST_TTL = 6 * 3600          # Claude 리스트 캐시 6시간
ACTIVE_WINDOW = 24 * 3600    # 최근 24시간 내 요청된 쿼리만 프리워밍
REFRESH_MARGIN = 15 * 60     # 만료 15분 전에 미리 갱신 → 활성 쿼리는 항상 따뜻함
PREWARM_INTERVAL = 5 * 60    # 프리워밍 점검 주기

_lock = threading.Lock()
_known: dict[str, dict] = {}  # key → {prompt, lang, last_access}

# 첫 화면(한국·시총·전체)과 미국 기본 화면은 부팅 직후부터 따뜻하게
_SEED_QUERIES = [
    ("한국 시가총액 상위 20개 종목을 웹에서 검색해서 최근 이슈와 투자 포인트를 정리해주세요. 정확히 20개 항목을 반환하세요.", "ko"),
    ("미국 시가총액 상위 20개 종목을 웹에서 검색해서 최근 이슈와 투자 포인트를 정리해주세요. 정확히 20개 항목을 반환하세요.", "ko"),
]


def _cache_key(prompt: str, lang: str) -> str:
    return hashlib.md5(f"{prompt}|{lang}".encode()).hexdigest()


def _get_cached_items(key: str) -> Optional[list[dict]]:
    entry = _list_cache.get(key)
    if entry and time.time() - entry[0] < LIST_TTL:
        return entry[1]
    return None


def _record_access(key: str, prompt: str, lang: str) -> None:
    with _lock:
        _known[key] = {"prompt": prompt, "lang": lang, "last_access": time.time()}


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


# ── Claude 호출: bare item(수치 없음) 스트리밍 ─────────────────────────────────

def _claude_items(prompt: str, lang: str):
    """Claude 웹 검색으로 종목 리스트를 생성, 완성된 item dict를 순서대로 yield (metrics 없음)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    lang_instruction = (
        "Write name, summary, badge in Korean."
        if lang == "ko"
        else "Write name, summary, badge in English."
    )
    system = SYSTEM_PROMPT + f" {lang_instruction}"

    client = anthropic.Anthropic(api_key=api_key)
    buffer = ""
    with client.messages.stream(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
    ) as stream:
        for text in stream.text_stream:
            buffer += text
            while True:
                result = _extract_next_item(buffer)
                if result is None:
                    break
                item, buffer = result
                item.pop("metrics", None)  # 수치는 캐시하지 않음 (항상 실시간)
                yield item


def _enrich(item: dict, is_korean: bool) -> str:
    """bare item에 실시간 metrics를 붙여 JSON 문자열로. (캐시 원본은 건드리지 않음)"""
    out = dict(item)
    out["metrics"] = _fetch_real_metrics(out.get("code", ""), is_korean)
    return json.dumps(out, ensure_ascii=False)


# ── 프리워밍: 활성 쿼리를 만료 전에 미리 갱신 ────────────────────────────────────

def _prewarm_loop():
    # 시드 쿼리를 활성 목록에 등록 → 부팅 직후 첫 사이클에 따뜻해짐
    for prompt, lang in _SEED_QUERIES:
        _record_access(_cache_key(prompt, lang), prompt, lang)

    while True:
        try:
            now = time.time()
            with _lock:
                snapshot = list(_known.items())
            for key, meta in snapshot:
                if now - meta["last_access"] > ACTIVE_WINDOW:
                    continue
                entry = _list_cache.get(key)
                age = now - entry[0] if entry else float("inf")
                if age <= LIST_TTL - REFRESH_MARGIN:
                    continue
                try:
                    fresh = list(_claude_items(meta["prompt"], meta["lang"]))
                    if fresh:
                        with _lock:
                            _list_cache[key] = (time.time(), fresh)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(PREWARM_INTERVAL)


threading.Thread(target=_prewarm_loop, daemon=True).start()


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

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    key = _cache_key(req.prompt, req.lang)
    _record_access(key, req.prompt, req.lang)
    is_korean = req.lang == "ko"

    def generate():
        # 아이템 생성(Claude 웹 검색 ~20초)은 별도 스레드에서 큐로 밀어넣고,
        # 메인 제너레이터는 큐가 조용하면 공백(keepalive)을 흘려보내 연결 유지.
        # 공백은 앱의 brace 파서가 무시하므로 안전.
        q: "queue.Queue" = queue.Queue()
        DONE = object()

        def producer():
            try:
                cached = _get_cached_items(key)
                if cached is not None:
                    for item in cached:
                        q.put(_enrich(item, is_korean))
                else:
                    collected = []
                    for item in _claude_items(req.prompt, req.lang):
                        collected.append(item)
                        q.put(_enrich(item, is_korean))
                    if collected:
                        with _lock:
                            _list_cache[key] = (time.time(), collected)
            except anthropic.RateLimitError:
                q.put('{"error":"rate_limit"}')
            except Exception as e:
                q.put(json.dumps({"error": str(e)}, ensure_ascii=False))
            finally:
                q.put(DONE)

        threading.Thread(target=producer, daemon=True).start()

        yield " "  # 즉시 첫 바이트 → TTFB 단축 + 연결 확립
        while True:
            try:
                payload = q.get(timeout=5)
            except queue.Empty:
                yield " "  # keepalive heartbeat
                continue
            if payload is DONE:
                break
            yield payload

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            # Render/프록시의 응답 버퍼링 비활성화 → 하트비트/카드가 실시간으로 흐름
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
