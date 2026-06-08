import time
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException

router = APIRouter()

URLS = {
    "kr": "https://companiesmarketcap.com/south-korea/largest-companies-in-south-korea-by-market-cap/",
    "us": "https://companiesmarketcap.com/usa/largest-companies-in-usa-by-market-cap/",
}

# { country: {"data": [...], "ts": float} }
_cache: dict = {}
CACHE_TTL = 30 * 60  # 30분


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _scrape(country: str, top_n: int) -> list:
    url = URLS.get(country)
    if not url:
        raise ValueError(f"Unknown country: {country}")

    # 최대 3회 재시도
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    else:
        raise last_err
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
    return stocks


def _get_cached(country: str, top_n: int) -> list:
    entry = _cache.get(country)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    # 캐시 만료 or 없음 → 새로 스크래핑
    data = _scrape(country, top_n)
    _cache[country] = {"data": data, "ts": time.time()}
    return data


@router.get("/{country}")
def get_stocks(country: str, top_n: int = 30):
    """
    country: "kr" | "us"
    top_n: 스크래핑할 최대 종목 수 (기본 30)
    캐시: 국가별 30분 인메모리 캐시
    """
    try:
        stocks = _get_cached(country, top_n)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"stocks": stocks}
