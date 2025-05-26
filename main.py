from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import datetime
import os
import re
from bs4 import BeautifulSoup

# 개인정보처리방침 안내
PRIVACY_URL = "https://github.com/KD-YOON/privacy-policy"
PRIVACY_NOTICE = "본 서비스의 개인정보 처리방침은 https://github.com/KD-YOON/privacy-policy 에서 확인할 수 있습니다. 신뢰할 수 있는 사이트 또는 항상 허용을 선택하셨다면 안내는 다시 나오지 않습니다."

def add_privacy_notice(data):
    # dict 결과에 개인정보처리방침 안내 필드 자동 추가
    if isinstance(data, dict):
        data['privacy_notice'] = PRIVACY_NOTICE
        data['privacy_policy_url'] = PRIVACY_URL
    return data

API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API 기반 실시간 조문·항·호 조회 + 분조(가지번호) 완전 자동화",
    version="7.0.1-article-branch-privacy"
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

def resolve_full_law_name(law_name: str) -> str:
    name = law_name.replace(" ", "").strip()
    for k, v in KNOWN_LAWS.items():
        if name == k.replace(" ", ""):
            return v
    return law_name

def normalize_law_name(name: str) -> str:
    return name.replace(" ", "").strip()

# 조문번호/가지번호 자동 분리
def parse_article_input(article_no_raw):
    if not article_no_raw:
        return None, None
    s = article_no_raw.replace(" ", "")
    m = re.match(r"제?(\d+)조의(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"제?(\d+)조", s)
    if m:
        return int(m.group(1)), None
    return None, None

def get_law_id(law_name: str, api_key: str) -> Optional[str]:
    normalized = normalize_law_name(law_name)
    try:
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "query": law_name,
            "pIndex": 1,
            "pSize": 10
        })
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        law_root = data.get("LawSearch") or data.get("lawSearch") or {}
        laws = law_root.get("laws", {}).get("law") or law_root.get("law")
        if not laws:
            return None
        if isinstance(laws, dict):
            laws = [laws]
        for law in laws:
            name_fields = [law.get("법령명한글", ""), law.get("법령약칭명", ""), law.get("법령명", "")]
            for name in name_fields:
                if normalize_law_name(name) == normalized:
                    return law.get("법령ID")
        for law in laws:
            if law.get("현행연혁코드") == "현행":
                return law.get("법령ID")
        return None
    except Exception as e:
        print("[lawId 오류]", e)
        return None

def fetch_article_html_fallback(law_name_full, article_no):
    try:
        law_url_name = quote(law_name_full.replace(' ', ''))
        article_url = f"https://www.law.go.kr/법령/{law_url_name}/제{article_no}"
        res = requests.get(article_url, timeout=7)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        main = soup.select_one(".law-article .article") or soup.select_one(".article") or soup.select_one(".law-article")
        text = main.get_text(separator="\n", strip=True) if main else "HTML에서 조문 본문을 찾을 수 없습니다."
        return text
    except Exception as e:
        return f"(HTML fallback 오류: {e})"

def extract_article_with_full(xml_text, article_no_raw, clause_no=None, subclause_no=None, law_name_full=None):
    circled_nums = {'①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10'}
    target_no, target_subno = parse_article_input(article_no_raw)
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("법령", {})
        articles = law.get("조문", {}).get("조문단위", [])
        if isinstance(articles, dict):
            articles = [articles]
        available = []
        for article in articles:
            no_raw = article.get("조문번호", "0")
            no = int(no_raw) if str(no_raw).isdigit() else 0
            subno_raw = article.get("조문가지번호")
            if subno_raw in [None, '', '0', 0]:
                subno = None
            elif str(subno_raw).isdigit():
                subno = int(subno_raw)
            else:
                try:
                    subno = int(str(subno_raw))
                except:
                    subno = None
            available.append(
                f"{no}조의{subno}" if subno is not None else f"{no}조"
            )
            if no == target_no and (
                subno == target_subno or
                (target_subno is None and subno is None)
            ):
                full_article = article.get("조문내용", "내용 없음")
                if not clause_no:
                    return full_article, full_article, available
                clauses = article.get("항", [])
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses:
                    cnum = clause.get("항번호", "").strip()
                    cnum_arabic = circled_nums.get(cnum, cnum)
                    if cnum_arabic == str(clause_no) or cnum == str(clause_no):
                        clause_content = clause.get("항내용", "내용 없음")
                        return clause_content, full_article, available
                return "요청한 항을 찾을 수 없습니다.", full_article, available
        if law_name_full and article_no_raw:
            html_text = fetch_article_html_fallback(law_name_full, article_no_raw)
            return f"(API에서 조문 미발견, HTML로 추출) {html_text}", html_text, available
        return (f"요청한 조문({article_no_raw})을 찾을 수 없습니다. (실제 조문번호: {', '.join(available)})", "", available)
    except Exception as e:
        return f"파싱 오류: {e}", "", []

def make_law_url(law_name_full, article_no=None):
    law_name_url = quote(law_name_full.replace(" ", ""))
    url = f"https://www.law.go.kr/법령/{law_name_url}"
    if article_no:
        url += f"/제{article_no}"
    return url

def make_markdown_table(law_name, article_no, clause_no, subclause_no, 내용, 법령링크, 조문전체, available_articles=None):
    내용_fmt = 내용.replace("|", "\\|").replace("\n", "<br>")
    조문전체_fmt = 조문전체.replace("|", "\\|").replace("\n", "<br>")
    tbl = (
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 법령명 | {law_name} |\n"
        f"| 조문 | {article_no or ''} |\n"
        f"| 항 | {str(clause_no)+'항' if clause_no else ''} |\n"
        f"| 호 | {str(subclause_no)+'호' if subclause_no else ''} |\n"
        f"| 내용 | {내용_fmt} |\n"
        f"| 조문 전체 | {조문전체_fmt} |\n"
        f"| 출처 | [국가법령정보센터 바로가기]({법령링크}) |\n"
    )
    if available_articles:
        tbl += f"| 조회가능 조문번호 | {', '.join(available_articles)} |\n"
    return tbl

@app.get("/")
@app.head("/")
def root():
    return add_privacy_notice({"message": "School LawBot API is running."})

@app.get("/healthz")
@app.head("/healthz")
def health_check():
    return add_privacy_notice({"status": "ok"})

@app.get("/ping")
@app.head("/ping")
def ping():
    return add_privacy_notice({"status": "ok"})

@app.get("/privacy-policy")
def privacy_policy():
    return add_privacy_notice({
        "message": "본 서비스의 개인정보 처리방침은 다음 링크에서 확인할 수 있습니다.",
        "url": PRIVACY_URL
    })

@app.get("/law", summary="법령 조문 조회")
@app.head("/law")
def get_law_clause(
    law_name: str = Query(None, example="학교폭력예방법시행령"),
    article_no: str = Query(None, example="제14조의 2"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    request: Request = None
):
    if not law_name or not article_no:
        return add_privacy_notice({
            "error": "law_name, article_no 파라미터는 필수입니다. 예시: /law?law_name=학교폭력예방법시행령&article_no=제14조의 2"
        })
    api_key = API_KEY
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "client_ip": request.client.host if request else "unknown",
        "law_name": law_name,
        "article_no": article_no,
        "clause_no": clause_no,
        "subclause_no": subclause_no,
        "api_key": api_key
    }
    try:
        law_name_full = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name_full, api_key)
        if not law_id:
            log_entry["status"] = "error"
            log_entry["error"] = "법령 ID 조회 실패"
            recent_logs.append(log_entry)
            if len(recent_logs) > 50:
                recent_logs.pop(0)
            return JSONResponse(content=add_privacy_notice({"error": "법령 ID 조회 실패"}), status_code=404)
        res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "ID": law_id,
            "pIndex": 1,
            "pSize": 1000
        })
        res.raise_for_status()
        if "법령이 없습니다" in res.text:
            log_entry["status"] = "error"
            log_entry["error"] = "해당 법령은 조회할 수 없습니다."
            recent_logs.append(log_entry)
            if len(recent_logs) > 50:
                recent_logs.pop(0)
            return JSONResponse(content=add_privacy_notice({"error": "해당 법령은 조회할 수 없습니다."}), status_code=403)
        내용, 조문전체, available_articles = extract_article_with_full(res.text, article_no, clause_no, subclause_no, law_name_full)
        law_url = make_law_url(law_name_full, article_no)
        markdown = make_markdown_table(law_name_full, article_no, clause_no, subclause_no, 내용, law_url, 조문전체, available_articles)
        result = {
            "source": "api",
            "출처": "lawService+HTMLfallback",
            "법령명": law_name_full,
            "조문": f"{article_no}" if article_no else "",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "조문전체": 조문전체,
            "법령링크": law_url,
            "markdown": markdown,
            "조문목록": available_articles
        }
        log_entry["status"] = "success"
        log_entry["result"] = result
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        return JSONResponse(content=add_privacy_notice(result))
    except Exception as e:
        log_entry["status"] = "error"
        log_entry["error"] = str(e)
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        print("🚨 API 에러:", e)
        return JSONResponse(content=add_privacy_notice({"error": "API 호출 실패"}), status_code=500)

@app.get("/test-log", summary="최근 요청 로그 10건 조회")
@app.head("/test-log")
def test_log():
    return add_privacy_notice({"recent_logs": recent_logs[-10:]})
