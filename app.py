import streamlit as st
import anthropic
import json
import re
import time
import requests
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="Stockpulse Select",
    page_icon="📈",
    layout="wide"
)

st.markdown("""
<style>
.badge-new {
    background: #e6f1fb; color: #185fa5;
    padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 500;
}
.badge-up {
    background: #eaf3de; color: #3b6d11;
    padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 500;
}
.badge-lev {
    background: #faeeda; color: #854f0b;
    padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 500;
}
</style>
""", unsafe_allow_html=True)

T = {
    "ko": {
        "settings": "⚙️ 설정",
        "api_key_label": "Anthropic API Key",
        "api_key_help": ".streamlit/secrets.toml에 저장하면 자동으로 불러옵니다.",
        "api_key_loaded": "✅ secrets.toml에서 키를 불러왔습니다.",
        "api_key_caption": "키는 로컬에서만 사용되며 저장되지 않습니다.",
        "how_to_use": "**사용법**",
        "how_steps": ["1. API 키 입력", "2. 국가 선택 후 탭 클릭", "3. 조건 설정 후 검색", "4. Claude가 최신 정보 수집"],
        "subtitle": "국내·미국 개별주 · ETF · 레버리지 ETF — Claude 웹검색 기반",
        "country_kr": "🇰🇷 한국",
        "country_us": "🇺🇸 미국",
        "tab_stocks": "🏢 개별주",
        "tab_etf": "📊 ETF",
        "tab_lev": "⚡ 레버리지 ETF",
        # 공통
        "search_btn": "🔍 검색",
        "no_api_key": "사이드바에서 API 키를 입력해주세요.",
        "spinner": "Claude가 웹 검색 중...",
        "no_results": "결과를 찾지 못했습니다.",
        "parse_error": "응답 파싱 실패",
        "rate_limit": "요청 한도 초과. {}초 후 자동 재시도합니다... ({}/3)",
        # KR 개별주
        "kr_stocks_header": "한국 개별주 — 시가총액 기준 (Top 10)",
        "sector": "섹터",
        "sector_opts": ["전체", "반도체/IT", "2차전지", "바이오/헬스케어", "금융", "자동차", "에너지", "소비재"],
        "market": "시장",
        "market_opts": ["코스피+코스닥", "코스피", "코스닥"],
        # KR ETF
        "kr_etf_header": "한국 ETF — 신규 상장 & 수익률 상승",
        "etf_type": "유형",
        "etf_type_opts": ["신규 상장 ETF", "꾸준히 수익률 상승", "테마별 추천"],
        "period": "기간",
        "period_opts": ["최근 1개월", "최근 3개월"],
        "keyword": "추가 키워드 (선택)",
        "keyword_placeholder_kr": "예: AI, 배당, 인도, 원자력 등",
        # KR 레버리지 ETF
        "kr_lev_header": "한국 레버리지 ETF — 2x · 인버스",
        "kr_lev_type": "유형",
        "kr_lev_type_opts": ["전체", "2배 레버리지", "인버스", "인버스 2배"],
        "kr_lev_sector": "섹터",
        "kr_lev_sector_opts": ["전체", "코스피200", "코스닥150", "나스닥", "반도체", "2차전지"],
        # US 개별주
        "us_stocks_header": "미국 개별주 — 시가총액 기준 (Top 10)",
        "us_sector": "섹터",
        "us_sector_opts": ["전체", "기술/AI", "반도체", "바이오/헬스케어", "금융", "에너지", "소비재", "전기차"],
        # US ETF
        "us_etf_header": "미국 ETF — 지수 & 테마",
        "us_etf_type": "유형",
        "us_etf_type_opts": ["지수 추종 ETF", "테마 ETF", "배당 ETF", "채권 ETF"],
        "keyword_placeholder_us": "예: AI, 클린에너지, 배당성장 등",
        # US 레버리지 ETF
        "us_lev_header": "미국 레버리지 ETF — 2x · 3x",
        "leverage": "레버리지 배율",
        "leverage_opts": ["2x · 3x 전체", "2배 레버리지", "3배 레버리지", "인버스 레버리지"],
        "sort_by": "정렬 기준",
        "sort_opts": ["수익률 높은 순", "운용자산(AUM) 큰 순", "거래량 많은 순"],
        "disclaimer": "⚠️ 본 앱은 투자 참고 정보 제공 목적이며, 투자 권유가 아닙니다. 투자 결정은 본인 판단과 책임 하에 진행하세요.",
    },
    "en": {
        "settings": "⚙️ Settings",
        "api_key_label": "Anthropic API Key",
        "api_key_help": "Save to .streamlit/secrets.toml to load automatically.",
        "api_key_loaded": "✅ Key loaded from secrets.toml",
        "api_key_caption": "Your key is only used locally and never stored.",
        "how_to_use": "**How to use**",
        "how_steps": ["1. Enter your API key", "2. Select country then tab", "3. Set filters and search", "4. Claude fetches latest data"],
        "subtitle": "KR & US Stocks · ETFs · Leveraged ETFs — Powered by Claude",
        "country_kr": "🇰🇷 Korea",
        "country_us": "🇺🇸 US",
        "tab_stocks": "🏢 Stocks",
        "tab_etf": "📊 ETFs",
        "tab_lev": "⚡ Leveraged ETFs",
        # common
        "search_btn": "🔍 Search",
        "no_api_key": "Please enter your API key in the sidebar.",
        "spinner": "Claude is searching the web...",
        "no_results": "No results found.",
        "parse_error": "Failed to parse response",
        "rate_limit": "Rate limit reached. Retrying in {}s... ({}/3)",
        # KR Stocks
        "kr_stocks_header": "KR Stocks — by Market Cap (Top 10)",
        "sector": "Sector",
        "sector_opts": ["All", "Semiconductor/IT", "Battery", "Bio/Healthcare", "Finance", "Auto", "Energy", "Consumer"],
        "market": "Market",
        "market_opts": ["KOSPI+KOSDAQ", "KOSPI", "KOSDAQ"],
        # KR ETF
        "kr_etf_header": "KR ETFs — New Listings & Rising Returns",
        "etf_type": "Type",
        "etf_type_opts": ["Newly Listed ETFs", "Consistently Rising", "Thematic Picks"],
        "period": "Period",
        "period_opts": ["Last 1 Month", "Last 3 Months"],
        "keyword": "Keyword (optional)",
        "keyword_placeholder_kr": "e.g. AI, dividend, India, nuclear...",
        # KR Leveraged ETF
        "kr_lev_header": "KR Leveraged ETFs — 2x · Inverse",
        "kr_lev_type": "Type",
        "kr_lev_type_opts": ["All", "2x Leveraged", "Inverse", "Inverse 2x"],
        "kr_lev_sector": "Underlying",
        "kr_lev_sector_opts": ["All", "KOSPI200", "KOSDAQ150", "Nasdaq", "Semiconductor", "Battery"],
        # US Stocks
        "us_stocks_header": "US Stocks — by Market Cap (Top 10)",
        "us_sector": "Sector",
        "us_sector_opts": ["All", "Tech/AI", "Semiconductor", "Bio/Healthcare", "Finance", "Energy", "Consumer", "EV"],
        # US ETF
        "us_etf_header": "US ETFs — Index & Thematic",
        "us_etf_type": "Type",
        "us_etf_type_opts": ["Index ETFs", "Thematic ETFs", "Dividend ETFs", "Bond ETFs"],
        "keyword_placeholder_us": "e.g. AI, clean energy, dividend growth...",
        # US Leveraged ETF
        "us_lev_header": "US Leveraged ETFs — 2x · 3x",
        "leverage": "Leverage",
        "leverage_opts": ["2x & 3x All", "2x Leveraged", "3x Leveraged", "Inverse Leveraged"],
        "sort_by": "Sort by",
        "sort_opts": ["Highest Return", "Largest AUM", "Highest Volume"],
        "disclaimer": "⚠️ For informational purposes only. Not financial advice. All investment decisions are your own responsibility.",
    }
}

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    lang = st.radio("🌐 Language", ["한국어", "English"], horizontal=True, key="lang")
    t = T["ko"] if lang == "한국어" else T["en"]

    st.title(t["settings"])
    saved_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    api_key = st.text_input(
        t["api_key_label"],
        value=saved_key,
        type="password",
        placeholder="sk-ant-...",
        help=t["api_key_help"]
    )
    if saved_key:
        st.caption(t["api_key_loaded"])
    else:
        st.caption(t["api_key_caption"])
    st.divider()
    st.markdown(t["how_to_use"])
    for step in t["how_steps"]:
        st.caption(step)

st.title("📈 Stockpulse Select")
st.caption(t["subtitle"])

_lang_instruction = "Write all text fields (name, summary, badge, label) in Korean." if lang == "한국어" else "Write all text fields in English."

SYSTEM_PROMPT = f"""You are a stock information analyst. Search the web for the latest data, then respond with ONLY a raw JSON object.

CRITICAL: Your response must ALWAYS be valid JSON only — no markdown, no code blocks, no explanations, no prose. Even if data is incomplete, output JSON with "N/A" for missing values. Never apologize or explain in text.

Schema: {{"items":[{{"name":"string","code":"string","summary":"2 sentences: recent news + investment point","metrics":[{{"label":"string","value":"string","positive":true|false|null}}],"badge":"string","badgeType":"up|new|lev|down"}}]}}

Rules: items≤10, metrics≤3, use the most recent data available. {_lang_instruction}"""


def scrape_stocks(country: str, top_n: int = 30) -> list:
    cache_key = f"scrape_{country}_{top_n}"
    cached = st.session_state.get(cache_key)
    if cached and time.time() - cached["ts"] < 600:
        return cached["data"]

    url_map = {
        "kr": "https://companiesmarketcap.com/south-korea/largest-companies-in-south-korea-by-market-cap/",
        "us": "https://companiesmarketcap.com/usa/largest-companies-in-usa-by-market-cap/",
    }
    resp = requests.get(url_map[country], headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    stocks = []
    for row in soup.select("table tbody tr")[:top_n]:
        name_el = row.select_one(".company-name")
        code_el = row.select_one(".company-code")
        if not name_el:
            continue
        mcap_tds = row.select("td.td-right")
        mcap = mcap_tds[1].text.strip() if len(mcap_tds) > 1 else ""
        price = mcap_tds[2].text.strip() if len(mcap_tds) > 2 else ""
        change_el = row.select_one(".percentage-green, .percentage-red")
        change = change_el.text.strip() if change_el else ""
        positive = ("percentage-green" in change_el.get("class", [])) if change_el else None
        stocks.append({
            "name": name_el.text.strip(),
            "code": code_el.text.strip() if code_el else "",
            "mcap": mcap,
            "price": price,
            "change": change,
            "change_positive": positive,
        })

    st.session_state[cache_key] = {"data": stocks, "ts": time.time()}
    return stocks


def extract_json(text: str) -> dict:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text.strip())


def call_claude(prompt: str, use_websearch: bool = True):
    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    if use_websearch:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]

    for attempt in range(3):
        try:
            full_text = ""
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    full_text += text
            return full_text
        except anthropic.RateLimitError:
            if attempt < 2:
                wait = 30 * (attempt + 1)
                st.warning(t["rate_limit"].format(wait, attempt + 1))
                time.sleep(wait)
            else:
                raise


def render_items(items: list):
    if not items:
        st.warning(t["no_results"])
        return
    cols = st.columns(2)
    for i, item in enumerate(items):
        with cols[i % 2]:
            badge_class = f"badge-{item.get('badgeType', 'up')}"
            badge_text = item.get('badge', '')
            code = item.get('code', '')
            name = item.get('name', '')
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**{name}** `{code}`" if code else f"**{name}**")
                with col2:
                    if badge_text:
                        st.markdown(f'<span class="{badge_class}">{badge_text}</span>', unsafe_allow_html=True)
                st.caption(item.get('summary', ''))
                metrics = item.get('metrics', [])
                if metrics:
                    mcols = st.columns(len(metrics))
                    for j, m in enumerate(metrics):
                        with mcols[j]:
                            val = m.get('value', '-')
                            label = m.get('label', '')
                            positive = m.get('positive')
                            color = "green" if positive is True else "red" if positive is False else None
                            st.markdown(
                                f"<div style='font-size:11px;color:#888;margin-bottom:2px'>{label}</div>"
                                f"<div style='font-size:14px;font-weight:500;{f'color:{color}' if color else ''}'>{val}</div>",
                                unsafe_allow_html=True)


def run(prompt: str, use_websearch: bool = True):
    with st.spinner(t["spinner"]):
        try:
            full_response = call_claude(prompt, use_websearch=use_websearch)
        except Exception as e:
            st.error(str(e))
            return
    try:
        parsed = extract_json(full_response)
        render_items(parsed.get("items", []))
    except Exception as e:
        st.error(f"{t['parse_error']}: {e}")
        st.code(full_response)


def run_with_scraped(country: str, count_num: int, sector: str, sector_all: str):
    with st.spinner(t["spinner"]):
        try:
            stocks = scrape_stocks(country, top_n=max(count_num, 30))
        except Exception as e:
            st.error(f"companiesmarketcap.com 스크래핑 실패: {e}")
            return
    stock_list = "\n".join(
        f"{i+1}. {s['name']} ({s['code']}) | 시총: {s['mcap']} | 가격: {s['price']} | 등락: {s['change']}"
        for i, s in enumerate(stocks[:count_num])
    )
    sector_str = "" if sector == sector_all else f"{sector} 섹터 종목만 선택해서 "
    country_str = "한국" if country == "kr" else "미국"
    prompt = (
        f"아래는 companiesmarketcap.com 기준 {country_str} 시가총액 상위 종목 데이터입니다.\n\n"
        f"{stock_list}\n\n"
        f"이 데이터를 바탕으로 {sector_str}상위 10개 종목의 최근 이슈와 투자 포인트를 JSON으로 정리해주세요. "
        f"시총·등락률 수치는 위 데이터 그대로 사용하세요."
    )
    run(prompt, use_websearch=False)


# ── Country selector ──────────────────────────────────────────
country = st.segmented_control(
    "country", [t["country_kr"], t["country_us"]],
    default=t["country_kr"], label_visibility="collapsed"
)
is_kr = (country == t["country_kr"])

tab_stocks, tab_etf, tab_lev = st.tabs([t["tab_stocks"], t["tab_etf"], t["tab_lev"]])

# ═══════════════════════════════════════════════════════════════
# 🇰🇷 KOREA
# ═══════════════════════════════════════════════════════════════
if is_kr:
    with tab_stocks:
        st.subheader(t["kr_stocks_header"])
        c1, c2 = st.columns([2, 1])
        with c1:
            sector = st.selectbox(t["sector"], t["sector_opts"], key="kr_sector")
        with c2:
            market = st.selectbox(t["market"], t["market_opts"], key="kr_market")

        if st.button(t["search_btn"], key="btn_kr_stocks", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                run_with_scraped("kr", 30, sector, t["sector_opts"][0])

    with tab_etf:
        st.subheader(t["kr_etf_header"])
        c1, c2 = st.columns(2)
        with c1:
            etf_type = st.selectbox(t["etf_type"], t["etf_type_opts"], key="kr_etf_type")
        with c2:
            etf_period = st.selectbox(t["period"], t["period_opts"], key="kr_etf_period")
        etf_theme = st.text_input(t["keyword"], placeholder=t["keyword_placeholder_kr"], key="kr_etf_theme")

        if st.button(t["search_btn"], key="btn_kr_etf", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                theme_str = f" '{etf_theme}' 관련" if etf_theme else ""
                period_kr = {"Last 1 Month": "최근 1개월", "Last 3 Months": "최근 3개월"}.get(etf_period, etf_period)
                idx = t["etf_type_opts"].index(etf_type)
                if idx == 0:
                    prompt = (f"{period_kr} 내에 KRX에 신규 상장된{theme_str} 국내 ETF를 알려주세요. "
                              f"상장일, 운용사, 투자 테마, 순자산총액, 수익률을 포함하세요.")
                elif idx == 1:
                    prompt = (f"{period_kr} 동안{theme_str} 꾸준히 수익률이 상승한 국내 ETF를 찾아주세요. "
                              f"수익률 추이, AUM, 투자 테마, 주요 편입 종목을 포함하세요.")
                else:
                    prompt = (f"현재 주목받는{theme_str} 국내 테마 ETF를 추천해주세요. "
                              f"{period_kr} 수익률, 운용사, 주요 종목, 투자 포인트를 포함하세요.")
                run(prompt)

    with tab_lev:
        st.subheader(t["kr_lev_header"])
        c1, c2 = st.columns(2)
        with c1:
            kr_lev_type = st.selectbox(t["kr_lev_type"], t["kr_lev_type_opts"], key="kr_lev_type")
        with c2:
            kr_lev_sector = st.selectbox(t["kr_lev_sector"], t["kr_lev_sector_opts"], key="kr_lev_sector")

        if st.button(t["search_btn"], key="btn_kr_lev", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                lev_all = t["kr_lev_type_opts"][0]
                sec_all = t["kr_lev_sector_opts"][0]
                lev_str = "" if kr_lev_type == lev_all else f"{kr_lev_type} "
                sec_str = "" if kr_lev_sector == sec_all else f"{kr_lev_sector} 관련 "
                prompt = (f"한국 국내 상장 {sec_str}{lev_str}레버리지 ETF를 수익률 높은 순으로 알려주세요. "
                          f"티커, 운용사, 최근 1개월 수익률, AUM, 추종 지수, 특징과 리스크를 포함하세요.")
                run(prompt)

# ═══════════════════════════════════════════════════════════════
# 🇺🇸 US
# ═══════════════════════════════════════════════════════════════
else:
    with tab_stocks:
        st.subheader(t["us_stocks_header"])
        us_sector = st.selectbox(t["us_sector"], t["us_sector_opts"], key="us_sector")

        if st.button(t["search_btn"], key="btn_us_stocks", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                run_with_scraped("us", 30, us_sector, t["us_sector_opts"][0])

    with tab_etf:
        st.subheader(t["us_etf_header"])
        c1, c2 = st.columns(2)
        with c1:
            us_etf_type = st.selectbox(t["us_etf_type"], t["us_etf_type_opts"], key="us_etf_type")
        with c2:
            us_etf_period = st.selectbox(t["period"], t["period_opts"], key="us_etf_period")
        us_etf_theme = st.text_input(t["keyword"], placeholder=t["keyword_placeholder_us"], key="us_etf_theme")

        if st.button(t["search_btn"], key="btn_us_etf", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                theme_str = f" '{us_etf_theme}' 관련" if us_etf_theme else ""
                period_kr = {"Last 1 Month": "최근 1개월", "Last 3 Months": "최근 3개월"}.get(us_etf_period, us_etf_period)
                idx = t["us_etf_type_opts"].index(us_etf_type)
                type_map = {
                    0: f"미국 지수 추종{theme_str} ETF를 추천해주세요. {period_kr} 수익률, AUM, 추종 지수, 특징을 포함하세요.",
                    1: f"현재 주목받는{theme_str} 미국 테마 ETF를 추천해주세요. {period_kr} 수익률, 운용사, 주요 종목, 투자 포인트를 포함하세요.",
                    2: f"미국 배당{theme_str} ETF를 추천해주세요. 배당수익률, 배당 주기, {period_kr} 수익률, 운용사, 특징을 포함하세요.",
                    3: f"미국 채권{theme_str} ETF를 추천해주세요. 금리 민감도, {period_kr} 수익률, AUM, 특징과 리스크를 포함하세요.",
                }
                run(type_map[idx])

    with tab_lev:
        st.subheader(t["us_lev_header"])
        c1, c2 = st.columns(2)
        with c1:
            lev_type = st.selectbox(t["leverage"], t["leverage_opts"], key="us_lev_type")
        with c2:
            us_lev_sector = st.selectbox(t["us_sector"], t["us_sector_opts"], key="us_lev_sector")
        sort_by = st.radio(t["sort_by"], t["sort_opts"], horizontal=True, key="us_sort")

        if st.button(t["search_btn"], key="btn_us_lev", use_container_width=True):
            if not api_key:
                st.error(t["no_api_key"])
            else:
                sec_all = t["us_sector_opts"][0]
                sector_str = "" if us_lev_sector == sec_all else f"{us_lev_sector} 관련 "
                lev_kr = {"2x · 3x 전체": "2x·3x 전체", "2x & 3x All": "2x·3x 전체",
                          "2배 레버리지": "2배 레버리지", "2x Leveraged": "2배 레버리지",
                          "3배 레버리지": "3배 레버리지", "3x Leveraged": "3배 레버리지",
                          "인버스 레버리지": "인버스 레버리지", "Inverse Leveraged": "인버스 레버리지"}.get(lev_type, lev_type)
                sort_kr = {"수익률 높은 순": "수익률 높은 순", "Highest Return": "수익률 높은 순",
                           "운용자산(AUM) 큰 순": "AUM 큰 순", "Largest AUM": "AUM 큰 순",
                           "거래량 많은 순": "거래량 많은 순", "Highest Volume": "거래량 많은 순"}.get(sort_by, sort_by)
                prompt = (f"미국 {sector_str}{lev_kr} ETF를 {sort_kr} 기준으로 알려주세요. "
                          f"티커, 운용사, 최근 1개월 수익률, AUM, 추종 지수, 특징과 리스크를 포함하세요.")
                run(prompt)

st.divider()
st.caption(t["disclaimer"])
