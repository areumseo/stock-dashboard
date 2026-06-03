# 📈 주식 정보 대시보드

Claude 웹검색 기반 주식 정보 조회 앱입니다.

## 기능
- 🏢 **국내 개별주**: 시가총액 기준 상위 종목 (섹터·시장·종목 수 필터)
- 📊 **국내 ETF**: 신규 상장 / 꾸준히 수익률 상승 / 테마별 추천
- ⚡ **미장 레버리지 ETF**: 2x·3x 레버리지 ETF 현황

## 설치 및 실행

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 앱 실행
```bash
streamlit run app.py
```

### 3. 브라우저에서 접속
```
http://localhost:8501
```

### 4. API 키 입력
- 사이드바에 Anthropic API 키 입력
- https://console.anthropic.com 에서 발급

## 주의사항
- API 호출 시 비용이 발생합니다 (검색 1회 약 수 원 수준)
- 본 앱은 투자 참고 정보 제공 목적이며 투자 권유가 아닙니다
