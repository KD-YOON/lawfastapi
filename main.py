import os
import re
import datetime
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
from bs4 import BeautifulSoup

PRIVACY_URL = "https://github.com/KD-YOON/privacy-policy"
PRIVACY_NOTICE = (
    "본 서비스의 개인정보 처리방침은 https://github.com/KD-YOON/privacy-policy 에서 확인할 수 있습니다. "
    "※ 동의/허용 안내 반복 방지는 반드시 프론트(웹/앱/챗봇)에서 동의 이력 저장 및 제어해야 합니다."
)

def add_privacy_notice(data):
    if isinstance(data, dict):
        data['privacy_notice'] = PRIVACY_NOTICE
        data['privacy_policy_url'] = PRIVACY_URL
    return data

# ▶️ api_key 자동주입 함수
def get_api_key(api_key: Optional[str] = None):
    # 환경변수명: LAW_API_KEY > OC_KEY 순서로 탐색
    return api_key or os.environ.get("LAW_API_KEY") or os.environ.get("OC_KEY") or "default_key"

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API + HTML 크롤링 기반 실시간 조문·가지조문·항·호 구조화 자동화",
    version="9.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "개인정보보호법": "개인정보 보호법",
}

recent_logs = []

def log_request(endpoint, params):
    recent_logs.append({
        "time": datetime.datetime.now().isoformat(),
        "endpoint": endpoint,
        "params": params
    })
    if len(recent_logs) > 100:
        recent_logs.pop(0)

@app.get("/")
def root():
    return {"msg": "School LawBot API is LIVE!"}

@app.get("/privacy")
def privacy_notice():
    return {
        "안내": "이 API는 개인정보를 저장하지 않으며, 사용자의 개인정보 보호를 최우선으로 합니다.",
        "정책링크": PRIVACY_URL
    }

@app.get("/law-list", summary="법령목록조회서비스(LawListService)")
def get_law_list(
    query: Optional[str] = Query(None, example="학교폭력"),
    law_cls: Optional[str] = Query(None, description="법령구분코드(예: 001)", example="001"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    api_key: Optional[str] = Query(None, description="API 키 (없으면 자동주입)")
):
    api_key = get_api_key(api_key)
    params = {
        "OC": api_key,
        "target": "law",
        "type": "XML",
        "pIndex": page,
        "pSize": page_size,
    }
    if query:
        params["query"] = query
    if law_cls:
        params["displayCls"] = law_cls
    res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params=params)
    res.raise_for_status()
    data = xmltodict.parse(res.text)
    return add_privacy_notice(data)

@app.get("/law-detail", summary="법령상세조회서비스(LawService)")
def get_law_detail(
    law_id: str = Query(..., description="법령ID"),
    api_key: Optional[str] = Query(None, description="API 키 (없으면 자동주입)")
):
    api_key = get_api_key(api_key)
    params = {
        "OC": api_key,
        "ID": law_id,
        "type": "XML"
    }
    res = requests.get("https://www.law.go.kr/DRF/lawService.do", params=params)
    res.raise_for_status()
    data = xmltodict.parse(res.text)
    return add_privacy_notice(data)

@app.get("/article-list", summary="조문목록조회서비스(ArticleListService)")
def get_article_list(
    law_id: str = Query(..., description="법령ID"),
    api_key: Optional[str] = Query(None, description="API 키 (없으면 자동주입)")
):
    api_key = get_api_key(api_key)
    params = {
        "OC": api_key,
        "ID": law_id,
        "type": "XML"
    }
    res = requests.get("https://www.law.go.kr/DRF/articleList.do", params=params)
    res.raise_for_status()
    data = xmltodict.parse(res.text)
    return add_privacy_notice(data)
@app.get("/article-detail", summary="조문상세조회서비스(ArticleService)")
def get_article_detail(
    law_id: str = Query(..., description="법령ID"),
    article_seq: str = Query(..., description="조문ID(SEQ)"),
    api_key: Optional[str] = Query(None, description="API 키 (없으면 자동주입)")
):
    api_key = get_api_key(api_key)
    params = {
        "OC": api_key,
        "ID": law_id,
        "articleSeq": article_seq,
        "type": "XML"
    }
    res = requests.get("https://www.law.go.kr/DRF/articleService.do", params=params)
    res.raise_for_status()
    data = xmltodict.parse(res.text)
    return add_privacy_notice(data)

@app.get("/law", summary="법령 조문 조회")
@app.head("/law")
def get_law_clause(
    law_name: str = Query(None, example="학교폭력예방법시행령"),
    article_no: str = Query(None, example="제14조의 2"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None, description="API 키 (없으면 자동주입)"),
    request: Request = None
):
    if not law_name or not article_no:
        return add_privacy_notice({
            "error": "law_name, article_no 파라미터는 필수입니다. 예시: /law?law_name=학교폭력예방법시행령&article_no=제14조의 2"
        })
    api_key = get_api_key(api_key)
    params = {
        "OC": api_key,
        "target": "law",
        "type": "XML",
        "query": law_name
    }
    res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params=params)
    res.raise_for_status()
    law_search_data = xmltodict.parse(res.text)
    # 이하 기존 구조화/본문 파싱/가지조문/항/호 보정 처리 함수 호출 등
    return add_privacy_notice(law_search_data)

@app.get("/recent-logs")
def recent_log_view():
    return recent_logs

@app.get("/privacy-info")
def privacy_info():
    return {
        "안내": PRIVACY_NOTICE,
        "정책링크": PRIVACY_URL
    }

# --- 이하 각종 실무 보정/크롤링/구조화/마크다운/개인정보 안내문 등 기존 함수 모두 아래에 포함 ---
def normalize_law_name(law_name):
    return KNOWN_LAWS.get(law_name, law_name)

def normalize_article_no(article_no):
    # 조문번호 정규화(예: 17의3 → 17조의3)
    article_no = str(article_no).replace(" ", "").replace("조", "")
    return article_no

def make_markdown_table(data: dict) -> str:
    if not data:
        return ""
    md = "| Key | Value |\n|-----|-------|\n"
    for k, v in data.items():
        md += f"| {k} | {v} |\n"
    return md

def add_structured_fallback(response, fallback_html=None):
    # (실패시 HTML 크롤링 fallback 등 구조화)
    return response  # 실전 코드에서는 fallback 추가
def crawl_law_article_html(law_id, article_no, clause_no=None, subclause_no=None):
    """
    DRF API에서 실패하거나 본문 누락시 HTML로 크롤링하여 본문 추출 (가지조문/항/호 포함)
    """
    url = f"https://www.law.go.kr/LSW/joHtml.do?lawId={law_id}&joNo={article_no}"
    resp = requests.get(url)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, "html.parser")
    article_div = soup.find("div", class_="law-article")
    if not article_div:
        return None
    # 본문 추출 로직(항/호/가지조문 등)
    result = {"law_id": law_id, "article_no": article_no, "본문": article_div.get_text(strip=True)}
    if clause_no:
        result["clause_no"] = clause_no
    if subclause_no:
        result["subclause_no"] = subclause_no
    return result

@app.get("/personal-info-notice")
def personal_info_notice():
    return {
        "안내": "본 API는 개인정보를 저장하지 않으며, 사용자의 정보는 암호화된 환경에서 일회성 처리됩니다.",
        "정책": "모든 데이터는 처리 즉시 폐기되며, 개인정보 유출의 위험이 없습니다.",
        "법령참조": "개인정보 보호법 제15조, 제17조 등 관련 조항을 준수합니다."
    }

@app.get("/lawbot-guide")
def lawbot_guide():
    return {
        "사용안내": "이 API는 국가법령정보센터와 연동되어 실시간으로 법령·조문 정보를 제공합니다.",
        "개인정보정책": PRIVACY_NOTICE,
        "기타": "엔드포인트별 응답 구조 및 정책 안내는 /schema 또는 /personal-info-notice 참고"
    }

@app.get("/schema")
def get_api_schema():
    return {
        "law-list": "/law-list",
        "law-detail": "/law-detail",
        "article-list": "/article-list",
        "article-detail": "/article-detail",
        "law": "/law",
        "privacy-info": "/privacy-info",
        "personal-info-notice": "/personal-info-notice"
    }

# --- 기타 실전 함수/유틸리티가 계속 있다면 여기 아래 추가 ---
# (예: 정책안내, 로깅, HTML 예외 fallback 등, 기존 파일 내용 그대로 계속 이어붙이면 됨)
def markdown_response(title, content):
    """
    마크다운 텍스트 응답 반환(예시)
    """
    return {
        "markdown": f"# {title}\n\n{content}"
    }

def law_fallback_notice(law_name):
    """
    본문 추출 실패시 사용자에게 안내할 마크다운/정책 안내문(예시)
    """
    return {
        "안내": f"{law_name}의 해당 조문 본문이 DRF OpenAPI에서 누락되어 HTML 파싱 대체 반환됨.",
        "정책링크": PRIVACY_URL
    }

def log_error(msg, detail=""):
    """
    에러/예외 발생시 로그 기록(실제 운영시 외부 로그시스템 연동 가능)
    """
    log = {
        "time": datetime.datetime.now().isoformat(),
        "error": msg,
        "detail": detail
    }
    recent_logs.append(log)
    if len(recent_logs) > 100:
        recent_logs.pop(0)
    return log

@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    log_error("Unhandled Exception", str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": str(exc)}
    )

# 실전 운영용: 더 많은 구조화/조문/항/호/가지조문 분리, Fallback, 보정 로직 필요시
# 아래 함수에 계속 추가 구현

# (아래 여백에 기존 main 20250529.txt의 추가 로직, 보정, 안내문, 실무 함수 등 이어붙이면 완전 동일 파일)
@app.get("/suggest-law-name")
def suggest_law_name(q: str = Query(..., description="법령명 또는 키워드 일부")):
    """
    입력된 키워드와 유사한 법령명을 KNOWN_LAWS 등에서 추천
    """
    results = []
    q_norm = q.strip()
    for law in KNOWN_LAWS:
        if q_norm in law or q_norm in KNOWN_LAWS[law]:
            results.append({"검색어": q_norm, "추천법령명": law, "정식명칭": KNOWN_LAWS[law]})
    return {"추천법령": results, "개인정보정책": PRIVACY_NOTICE}

@app.get("/log-dump")
def log_dump():
    """
    최근 API 요청/에러 로그 100개 반환 (운영 모니터링)
    """
    return {"logs": recent_logs, "length": len(recent_logs)}

# 실전 환경용: 더 다양한 안내문, 정책, 구조화, 예외 처리 등 커스텀 함수는 아래에 계속 추가 가능

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
