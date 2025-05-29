import os
import re
import datetime
import requests
import xmltodict
from fastapi import FastAPI, Query, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
from time import time

app = FastAPI(
    title="School LawBot (모든 기능+UX설명 통합)",
    description="""
    - 즉시 반환(본문/요약/구조/조문목록/UX/에러/누락/메타/링크/모바일 등)
    - 비동기 AI 요약(후처리/캐싱)
    - UX/가이드/관련조문/유사조문/입력정규화/프라이버시/에러 안내/누락 자동 탐지
    - 코드 주석/설명/확장성/테스트용 health check/head 대응/프론트 UX까지 아낌없이 통합
    """,
    version="99.9.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------- [캐싱: 메모리(운영은 Redis 권장)] ----------
law_cache = {}       # 즉시 반환 캐시
ai_summary_cache = {} # AI 요약 캐시

def cache_set(cache, key, value, ttl=3600):
    """캐시 저장 (ttl=초단위)"""
    cache[key] = (value, time() + ttl)

def cache_get(cache, key):
    """캐시 조회 (만료되면 삭제)"""
    val = cache.get(key)
    if val and val[1] > time():
        return val[0]
    elif val:
        cache.pop(key, None)
    return None

# ---------- [공통 메타/UX/가이드/에러 안내 함수] ----------
def add_privacy_notice(data):
    """응답에 프라이버시, 도움말 등 추가"""
    data['privacy_notice'] = "https://github.com/KD-YOON/privacy-policy"
    data['help_url'] = "https://github.com/KD-YOON/lawbot-help"
    data['developer'] = "https://github.com/KD-YOON"
    return data

def user_guide():
    return (
        "법령명/조문번호 예시: '학교폭력예방법 시행령', '14', '14의2', '제14조', '제14조의2', 띄어쓰기 무시"
    )

# ---------- [입력 자동 정규화(조문번호)] ----------
def fix_article_no(article_no):
    """사용자 입력 조문번호를 표준화(제14조의2 등)"""
    s = str(article_no).replace(" ", "")
    if re.match(r'^제\d+조(의\d+)?$', s):
        return s
    if s.isdigit():
        return f'제{s}조'
    m = re.match(r"^(\d+)의(\d+)$", s)
    if m:
        return f"제{m.group(1)}조의{m.group(2)}"
    if not s.startswith('제'):
        s = '제' + s
    if not ('조' in s):
        s = s + '조'
    return s

# ---------- [법령별 공식 링크 생성] ----------
def make_article_link(law_name, article_no):
    """국가법령정보센터의 조문별 직접 링크 생성(없는 조문도 생성)"""
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    if article_no:
        article_path = quote(article_no, safe='')
        return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"
    else:
        return f"https://www.law.go.kr/법령/{law_url_name}"

# ---------- [법령명 → DRF API 법령ID 찾기] ----------
def get_law_id(law_name, api_key):
    """공식 DRF API로 법령ID 조회"""
    try:
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": api_key, "target": "law", "type": "XML", "query": law_name, "pIndex": 1, "pSize": 10
        })
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        laws = data.get("LawSearch", {}).get("laws", {}).get("law")
        if not laws:
            return None
        if isinstance(laws, dict): laws = [laws]
        for law in laws:
            if law.get("현행연혁코드") == "현행":
                return law.get("법령ID")
        return laws[0].get("법령ID")
    except Exception:
        return None

# ---------- [조문 구조화: 항/호/가지조문 트리 변환] ----------
def split_article_text_to_structure(text):
    """
    조문 본문을 항/호/가지조문별 트리 구조 JSON 변환.
    (실무에서는 추가로 별표/부칙 등 확장 가능)
    """
    gaji_pattern = re.compile(r'(제\d+조의\d+)[\s:.\)]*')
    hang_pattern = re.compile(r'(제\d+항)[\s:.\)]*')
    ho_pattern = re.compile(r'(제\d+호)[\s:.\)]*')
    result = {}
    gaji_splits = gaji_pattern.split(text)
    if len(gaji_splits) > 1:
        for i in range(1, len(gaji_splits), 2):
            gaji_title = gaji_splits[i]
            gaji_content = gaji_splits[i+1] if i+1 < len(gaji_splits) else ""
            result[gaji_title] = split_article_text_to_structure(gaji_content)
        return result
    hang_splits = hang_pattern.split(text)
    if len(hang_splits) > 1:
        hang_dict = {}
        preface = hang_splits[0]
        for i in range(1, len(hang_splits), 2):
            hang_title = hang_splits[i]
            hang_content = hang_splits[i+1] if i+1 < len(hang_splits) else ""
            ho_splits = ho_pattern.split(hang_content)
            if len(ho_splits) > 1:
                ho_dict = {}
                ho_preface = ho_splits[0]
                for j in range(1, len(ho_splits), 2):
                    ho_title = ho_splits[j]
                    ho_content = ho_splits[j+1] if j+1 < len(ho_splits) else ""
                    ho_dict[ho_title] = ho_content.strip()
                hang_dict[hang_title] = {'본문': ho_preface.strip(), '호': ho_dict}
            else:
                hang_dict[hang_title] = hang_content.strip()
        result = {'머릿말': preface.strip(), '항': hang_dict}
        return result
    return text.strip()

# ---------- [조문/본문/목록/구조화+누락 자동감지+UX안내까지] ----------
def extract_article(xml_text, article_no_raw):
    """
    - DRF 법령 XML에서 특정 조문 본문/목록/구조 추출
    - 없는 조문 자동 탐지, 파싱 예외 자동 안내
    """
    try:
        data = xmltodict.parse(xml_text)
        articles = []
        for k in ["조문단위", "가지조문단위"]:
            part = data.get("법령", {}).get("조문", {}).get(k)
            if part:
                if isinstance(part, dict): part = [part]
                articles.extend(part)
        available = [art.get("조문번호", "") for art in articles]
        for article in articles:
            if article.get("조문번호", "") == article_no_raw:
                body = article.get("조문내용", "")
                구조화 = split_article_text_to_structure(body)
                return body, available, 구조화
        return "", available, None
    except Exception as e:
        return f"파싱 오류: {e}", [], None

# ---------- [1️⃣ 즉시 반환: 조문/구조/목록/UX/메타/누락/링크 등 올인] ----------
@app.get("/law")
def law(
    law_name: str = Query(..., description="법령명", example="학교폭력예방법 시행령"),
    article_no: str = Query(..., description="조문번호", example="14조의2"),
    device: Optional[str] = Query(None, description="mobile/pc"),
    request: Request = None,
    background_tasks: BackgroundTasks = None
):
    start_time = time()
    api_key = os.environ.get("OC_KEY", "default_key")
    fixed_article_no = fix_article_no(article_no)
    cache_key = f"{law_name}:{fixed_article_no}"

    # [캐싱 우선 반환]
    cached = cache_get(law_cache, cache_key)
    if cached:
        result = cached
        result["from_cache"] = True
        result["processingTime"] = "%.2fs" % (time() - start_time)
        return add_privacy_notice(result)

    # [법령ID 조회 → 없는 경우 UX 안내]
    law_id = get_law_id(law_name, api_key)
    if not law_id:
        return add_privacy_notice({
            "found": False, "message": f"법령명을 찾을 수 없습니다: '{law_name}'",
            "guide": user_guide(),
            "directLink": make_article_link(law_name, None),
            "from_cache": False,
            "viewType": "responsive-card" if (device == "mobile") else "table"
        })

    # [조문 본문/구조/목록 추출]
    res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
        "OC": api_key, "target": "law", "type": "XML", "ID": law_id, "pIndex": 1, "pSize": 1000
    })
    body, available, 구조화 = extract_article(res.text, fixed_article_no)
    found = bool(body and "없음" not in body and len(body.strip()) > 5)
    direct_link = make_article_link(law_name, fixed_article_no)
    summary = body[:100].replace('\n', ' ') + ("..." if len(body) > 100 else "")

    # [UX/누락/유사조문 안내]
    message = (
        "정상적으로 조회되었습니다." if found else
        f"요청하신 '{law_name} {fixed_article_no}'은 존재하지 않습니다.\n"
        + (f"\n📌 현재 {law_name}에는 다음과 같은 유사 조문이 존재합니다:\n- " + "\n- ".join(available) if available else "") +
        "\n혹시 다른 조문(예: 최근 개정)이나 제도를 찾으시면 다시 입력해 주세요."
    )

    result = {
        "lawName": law_name,
        "articleNo": fixed_article_no,
        "userInput": article_no,
        "found": found,
        "message": message,
        "articleContent": body if found else "",
        "summary": summary,
        "structure": 구조화,
        "articleList": available,
        "guide": user_guide(),
        "directLink": direct_link,
        "viewType": "responsive-card" if (device == "mobile") else "table",
        "from_cache": False,
        "processingTime": "%.2fs" % (time() - start_time),
        "lastUpdated": datetime.datetime.now().isoformat()
    }
    cache_set(law_cache, cache_key, result)
    # [비동기 부가(AI 요약) 예약]
    if found:
        background_tasks.add_task(ai_summary_task, law_name, fixed_article_no, body)
    return add_privacy_notice(result)

# ---------- [2️⃣ 비동기 부가기능: AI 요약, 캐싱] ----------
def ai_summary_task(law_name, article_no, body):
    """
    비동기 AI 요약 (실제론 OpenAI/Claude 등 LLM API 연동)
    운영환경에서는 외부 API+캐싱 조합으로 개선 권장
    """
    import time as t
    t.sleep(2)  # 외부 LLM 호출 시뮬레이션
    ai_summary = f"[AI요약] '{body[:80]}...'의 핵심 내용입니다."
    cache_set(ai_summary_cache, f"{law_name}:{article_no}", ai_summary)

@app.get("/law/ai-summary")
def law_ai_summary(
    law_name: str = Query(..., description="법령명", example="학교폭력예방법 시행령"),
    article_no: str = Query(..., description="조문번호", example="14조의2")
):
    key = f"{law_name}:{fix_article_no(article_no)}"
    ai_summary = cache_get(ai_summary_cache, key)
    if ai_summary:
        return {
            "lawName": law_name, "articleNo": article_no, "aiSummary": ai_summary,
            "status": "ok"
        }
    return {
        "lawName": law_name, "articleNo": article_no,
        "aiSummary": "AI 요약 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        "status": "pending"
    }

# ---------- [3️⃣ 기타 안내/UX/테스트/메타/health check] ----------
@app.get("/")
@app.head("/")
def root():
    return {
        "message": "School LawBot API (풀옵션/UX) Running",
        "guide": user_guide(),
        "features": [
            "즉시 반환(본문/요약/구조화/목록/UX 안내/링크/메타)",
            "비동기 부가기능(AI 요약/후처리)",
            "캐싱(메모리/Redis/DB)",
            "모바일/PC viewType, UX 최적화",
            "없는 조문도 링크 제공, 에러/누락/유사조문 UX 안내",
            "개발자, 프라이버시, 도움말, 에러/누락 자동안내"
        ]
    }

@app.get("/healthz")
@app.head("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/privacy-policy")
def privacy_policy():
    return {"privacy_notice": "https://github.com/KD-YOON/privacy-policy"}

