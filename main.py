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
    title="School LawBot (풀 옵션)",
    description="즉시 반환 + 비동기 부가 + 캐싱 + 모든 UX 메타/구조화 안내 포함",
    version="12.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 캐싱 (운영: Redis/DB로 교체 권장) ----
law_cache = {}
ai_summary_cache = {}

def cache_set(cache, key, value, ttl=3600):
    cache[key] = (value, time() + ttl)

def cache_get(cache, key):
    val = cache.get(key)
    if val and val[1] > time():
        return val[0]
    elif val:
        cache.pop(key, None)
    return None

# ---- 유틸/메타/정규화 ----
def add_privacy_notice(data):
    data['privacy_notice'] = "https://github.com/KD-YOON/privacy-policy"
    return data

def fix_article_no(article_no):
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

def make_article_link(law_name, article_no):
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    if article_no:
        article_path = quote(article_no, safe='')
        return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"
    else:
        return f"https://www.law.go.kr/법령/{law_url_name}"

def get_law_id(law_name, api_key):
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

# ---- 조문 구조화 (항/호 등) ----
def split_article_text_to_structure(text):
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

# ---- 조문 전체/목록/본문/구조 추출 ----
def extract_article(xml_text, article_no_raw):
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

# ---- 1️⃣ 즉시 반환 (풀 UX/구조/메타 포함) ----
@app.get("/law")
def law(
    law_name: str = Query(..., description="법령명", example="학교폭력예방법 시행령"),
    article_no: str = Query(..., description="조문번호", example="14조의2"),
    device: Optional[str] = Query(None, description="모바일/PC 구분"),
    request: Request = None,
    background_tasks: BackgroundTasks = None
):
    start_time = time()
    api_key = os.environ.get("OC_KEY", "default_key")
    fixed_article_no = fix_article_no(article_no)
    cache_key = f"{law_name}:{fixed_article_no}"
    cached = cache_get(law_cache, cache_key)
    if cached:
        result = cached
        result["from_cache"] = True
        result["processingTime"] = "%.2fs" % (time() - start_time)
        return add_privacy_notice(result)

    law_id = get_law_id(law_name, api_key)
    if not law_id:
        return add_privacy_notice({
            "found": False, "message": "법령명을 찾을 수 없습니다.",
            "guide": "법령명을 정확히 입력하세요. 예: 학교폭력예방법 시행령",
            "directLink": make_article_link(law_name, None),
            "from_cache": False,
            "viewType": "responsive-card" if (device == "mobile") else "table"
        })

    res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
        "OC": api_key, "target": "law", "type": "XML", "ID": law_id, "pIndex": 1, "pSize": 1000
    })
    body, available, 구조화 = extract_article(res.text, fixed_article_no)
    found = bool(body and "없음" not in body and len(body.strip()) > 5)
    direct_link = make_article_link(law_name, fixed_article_no)
    summary = body[:100].replace('\n', ' ') + ("..." if len(body) > 100 else "")

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
        "guide": "‘14’, ‘14의2’, ‘제14조의2’ 등 자유 입력 가능. 띄어쓰기는 무시됩니다.",
        "directLink": direct_link,   # 없는 조문이어도 무조건 생성
        "viewType": "responsive-card" if (device == "mobile") else "table",
        "from_cache": False,
        "processingTime": "%.2fs" % (time() - start_time),
        "lastUpdated": datetime.datetime.now().isoformat()
    }
    cache_set(law_cache, cache_key, result)
    # 부가기능(AI 요약) 비동기 작업 예약
    if found:
        background_tasks.add_task(ai_summary_task, law_name, fixed_article_no, body)
    return add_privacy_notice(result)

# ---- 2️⃣ 비동기 부가기능(AI 요약 등) ----
def ai_summary_task(law_name, article_no, body):
    # 실제론 OpenAI/Claude 등 LLM API 호출(아래는 데모)
    import time as t
    t.sleep(2)  # 외부 LLM API 호출 시간 시뮬레이션
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

# ---- 3️⃣ 기타 안내/UX ----
@app.get("/")
def root():
    return {
        "message": "School LawBot API (풀 옵션) Running",
        "guide": "법령명+조문번호로 /law 조회, AI요약은 /law/ai-summary",
        "features": [
            "즉시 반환(본문/요약/구조화/목록/UX 안내/링크/메타)",
            "비동기 부가기능(AI 요약/후처리)",
            "캐싱(메모리/Redis/DB)",
            "모바일/PC viewType, UX 최적화",
            "없는 조문도 링크 제공, 에러/누락 UX 안내"
        ]
    }

@app.get("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/privacy-policy")
def privacy_policy():
    return {"privacy_notice": "https://github.com/KD-YOON/privacy-policy"}

