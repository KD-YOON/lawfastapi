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

API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API 기반 실시간 조문·항·호 조회 + 분조(가지번호) 완전 자동화",
    version="7.2.0"
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
        article_url = f"https://www.law.go.kr/법령/{law_url_name}/제{str(article_no).replace(' ','')}"
        res = requests.get(article_url, timeout=7)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        selectors = [
            ".law-article .article",
            ".article",
            ".law-article",
            "#article",
            ".cont_article",
        ]
        main = None
        for sel in selectors:
            main = soup.select_one(sel)
            if main:
                break
        text = main.get_text(separator="\n", strip=True) if main else "HTML에서 조문 본문을 찾을 수 없습니다."
        return text
    except Exception as e:
        return f"(HTML fallback 오류: {e})"

def extract_article_with_full(xml_text, article_no_raw, clause_no=None, subclause_no=None, law_name_full=None):
    circled_nums = {'①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10'}
    target_no, target_subno = parse_article_input(article_no_raw)
    canonical_article_no = None
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("법령", {})
        articles = law.get("조문", {}).get("조문단위", [])
        if isinstance(articles, dict):
            articles = [articles]
        available = []
        for article in articles:
            no_raw = str(article.get("조문번호", "0"))
            subno_raw = article.get("조문가지번호")
            no_m = re.match(r"(\d+)조의(\d+)", no_raw)
            if no_m:
                no = int(no_m.group(1))
                subno = int(no_m.group(2))
            else:
                no = int(no_raw) if no_raw.isdigit() else 0
                if subno_raw in [None, '', '0', 0]:
                    subno = None
                elif str(subno_raw).isdigit():
                    subno = int(subno_raw)
                else:
                    try:
                        subno = int(str(subno_raw))
                    except:
                        subno = None
            this_article_name = f"제{no}조의{subno}" if subno is not None else f"제{no}조"
            available.append(this_article_name)
            if no == target_no and (
                subno == target_subno or
                (target_subno is None and subno is None)
            ):
                canonical_article_no = this_article_name
                full_article = article.get("조문내용", "내용 없음")
                if not clause_no:
                    return full_article, full_article, available, canonical_article_no
                clauses = article.get("항", [])
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses:
                    cnum = clause.get("항번호", "").strip()
                    cnum_arabic = circled_nums.get(cnum, cnum)
                    if cnum_arabic == str(clause_no) or cnum == str(clause_no):
                        clause_content = clause.get("항내용", "내용 없음")
                        return clause_content, full_article, available, canonical_article_no
                return "요청한 항을 찾을 수 없습니다.", full_article, available, canonical_article_no
        # 조문 본문 추출 실패 시
        if law_name_full and article_no_raw:
            html_text = fetch_article_html_fallback(law_name_full, article_no_raw)
            canonical_article_no = None  # fallback은 입력값 그대로 씀
            return (
                f"해당 조문(가지조문 등)은 시스템에서 자동 추출이 불가합니다.<br>"
                f"아래 국가법령정보센터 바로가기를 확인해 주세요.",
                "",
                available,
                canonical_article_no
            )
        return (
            f"요청한 조문({article_no_raw})을 찾을 수 없습니다. (실제 조문번호: {', '.join(available)})",
            "",
            available,
            None
        )
    except Exception as e:
        return f"파싱 오류: {e}", "", [], None

def make_law_url(law_name_full, article_no=None):
    law_name_url = quote(law_name_full.replace(" ", ""))
    url = f"https://www.law.go.kr/법령/{law_name_url}"
    if article_no:
        art = str(article_no).replace(" ", "")
        url += f"/{art}"
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
        # --- 본문, canonical_article_no(실제 존재하는 조문명) 함께 반환!
        내용, 조문전체, available_articles, canonical_article_no = extract_article_with_full(
            res.text, article_no, clause_no, subclause_no, law_name_full
        )
        # --- 링크는 반드시 실제 파싱된 조문명(없으면 입력값 fallback)으로 생성
        law_url = make_law_url(law_name_full, canonical_article_no or article_no)
        # --- 내용이 신뢰불가면 안내문구+링크만 안내
        if not 내용 or 내용 in [
            "내용 없음",
            "요청한 조문을 찾을 수 없습니다.",
            "HTML에서 조문 본문을 찾을 수 없습니다."
        ] or 내용.startswith("(API에서 조문 미발견") or 내용.startswith("해당 조문(가지조문 등)은 시스템에서"):
            안내 = (
                f"해당 조문(가지조문 등)은 시스템에서 자동 추출이 불가합니다.<br>"
                f"아래 국가법령정보센터 바로가기에서 직접 확인해 주세요."
            )
            내용 = 안내
            조문전체 = ""
        markdown = make_markdown_table(
            law_name_full, canonical_article_no or article_no,
            clause_no, subclause_no, 내용, law_url, 조문전체, available_articles
        )
        result = {
            "source": "api",
            "출처": "lawService+HTMLfallback",
            "법령명": law_name_full,
            "조문": f"{canonical_article_no or article_no}" if article_no else "",
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
