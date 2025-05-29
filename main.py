import os
import re
import datetime
import requests
import xmltodict
from fastapi import FastAPI, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
from time import time

app = FastAPI(
    title="School LawBot (일관성-정확성-최우선)",
    description="정확히 일치하는 조문만 본문/구조 제공, 없으면 안내/유사조문/링크만 안내, AI/유사조문 등은 별도 API",
    version="15.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

def add_privacy_notice(data):
    data['privacy_notice'] = "https://github.com/KD-YOON/privacy-policy"
    data['help_url'] = "https://github.com/KD-YOON/lawbot-help"
    data['developer'] = "https://github.com/KD-YOON"
    return data

def user_guide():
    return (
        "법령명/조문번호 예시: '학교폭력예방법 시행령', '14', '14의2', '제14조', '제14조의2', 띄어쓰기 무시"
    )

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

def extract_article(xml_text, article_no_raw):
    """
    - 완전 일치하는 경우만 본문/구조 반환
    - 없으면 본문 없음, 안내/유사조문/링크만 제공
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
        # 1. 완전일치만 본문/구조 반환
        for article in articles:
            if article.get("조문번호", "") == article_no_raw:
                body = article.get("조문내용", "")
                구조화 = split_article_text_to_structure(body)
                return body, available, 구조화
        # 2. 아예 없으면 본문 없음, 안내만
        return "", available, None
    except Exception as e:
        return f"파싱 오류: {e}", [], None

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

    cached = cache_get(law_cache, cache_key)
    if cached:
        result = cached
        result["from_cache"] = True
        result["processingTime"] = "%.2fs" % (time() - start_time)
        return add_privacy_notice(result)

    law_id = get_law_id(law_name, api_key)
    if not law_id:
        return add_privacy_notice({
            "found": False, "message": f"법령명을 찾을 수 없습니다: '{law_name}'",
            "guide": user_guide(),
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

    if found:
        message = "정상적으로 조회되었습니다."
    else:
        message = (
            f"정확히 일치하는 '{law_name} {fixed_article_no}' 조문은 존재하지 않습니다."
            + (f"\n📌 현재 {law_name}에는 다음과 같은 유사 조문이 존재합니다:\n- " + "\n- ".join(available) if available else "")
            + f"\n사이트에서 직접 확인: {direct_link}"
        )

    result = {
        "lawName": law_name,
        "articleNo": fixed_article_no,
        "userInput": article_no,
        "found": found,
        "message": message,
        "articleContent": body if found else "",
        "summary": summary if found else "",
        "structure": 구조화 if found else None,
        "articleList": available,
        "guide": user_guide(),
        "directLink": direct_link,
        "viewType": "responsive-card" if (device == "mobile") else "table",
        "from_cache": False,
        "processingTime": "%.2fs" % (time() - start_time),
        "lastUpdated": datetime.datetime.now().isoformat()
    }
    cache_set(law_cache, cache_key, result)
    # AI 요약은 본문이 정확히 있을 때만 실행
    if found:
        background_tasks.add_task(ai_summary_task, law_name, fixed_article_no, body)
    return add_privacy_notice(result)

def ai_summary_task(law_name, article_no, body):
    """
    비동기 AI 요약 (실제론 OpenAI/Claude 등 LLM API 연동)
    운영환경에서는 외부 API+캐싱 조합으로 개선 권장
    """
    import time as t
    t.sleep(2)
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

@app.get("/")
@app.head("/")
def root():
    return {
        "message": "School LawBot API (일관성-정확성-최우선) Running",
        "guide": user_guide(),
        "features": [
            "정확히 일치하는 조문만 본문/구조 제공",
            "없으면 안내/유사조문/사이트 링크만 안내",
            "AI/유사조문 등은 별도 API로 분리",
            "모바일/PC viewType, UX 최적화",
            "없는 조문도 링크 제공, 캐싱, 에러/누락 안내"
        ]
    }

@app.get("/healthz")
@app.head("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/privacy-policy")
def privacy_policy():
    return {"privacy_notice": "https://github.com/KD-YOON/privacy-policy"}
