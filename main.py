from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import datetime
import os

API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API 기반 실시간 조문·항·호 조회 서비스 + 마크다운 테이블 반환",
    version="5.3.2-urlfix-markdown"
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
    # 추가 약칭은 여기!
}

recent_logs = []

@app.get("/")
@app.head("/")
def root():
    return {"message": "School LawBot API is running."}

@app.get("/healthz")
@app.head("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/ping")
@app.head("/ping")
def ping():
    return {"status": "ok"}

@app.get("/privacy-policy")
def privacy_policy():
    return {
        "message": "본 서비스의 개인정보 처리방침은 다음 링크에서 확인할 수 있습니다.",
        "url": "https://YOURDOMAIN.com/privacy-policy"
    }

def resolve_full_law_name(law_name: str) -> str:
    name = law_name.replace(" ", "").strip()
    for k, v in KNOWN_LAWS.items():
        if name == k.replace(" ", ""):
            return v
    return law_name

def normalize_law_name(name: str) -> str:
    return name.replace(" ", "").strip()

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

def extract_article(xml_text, article_no, clause_no=None, subclause_no=None):
    circled_nums = {'①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10'}
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("법령", {})
        articles = law.get("조문", {}).get("조문단위", [])
        if isinstance(articles, dict):
            articles = [articles]
        for article in articles:
            if article.get("조문번호") == str(article_no):
                if not clause_no:
                    return article.get("조문내용", "내용 없음")
                clauses = article.get("항", [])
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses:
                    cnum = clause.get("항번호", "").strip()
                    cnum_arabic = circled_nums.get(cnum, cnum)
                    if cnum_arabic == str(clause_no) or cnum == str(clause_no):
                        if not subclause_no:
                            return clause.get("항내용", "내용 없음")
                        subclauses = clause.get("호", [])
                        if isinstance(subclauses, dict):
                            subclauses = [subclauses]
                        for sub in subclauses:
                            sub_num = sub.get("호번호", "").replace(".", "")
                            if sub_num == str(subclause_no):
                                return sub.get("호내용", "내용 없음")
                        return "요청한 호를 찾을 수 없습니다."
                return "요청한 항을 찾을 수 없습니다."
        return "요청한 조문을 찾을 수 없습니다."
    except Exception as e:
        return f"파싱 오류: {e}"

# 🚩 출처 링크: 한글만 quote, 조문까지만!
def make_law_url(law_name_full, article_no=None):
    law_name_url = quote(law_name_full.replace(" ", ""))
    url = f"https://www.law.go.kr/법령/{law_name_url}"
    if article_no:
        url += f"/제{article_no}조"
    return url

def make_markdown_table(law_name, article_no, clause_no, subclause_no, 내용, 법령링크):
    # 파이프, 줄바꿈 등 마크다운 안전처리
    내용_fmt = 내용.replace("|", "\\|").replace("\n", "<br>")
    return (
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 법령명 | {law_name} |\n"
        f"| 조문 | {'제'+str(article_no)+'조' if article_no else ''} |\n"
        f"| 항 | {str(clause_no)+'항' if clause_no else ''} |\n"
        f"| 호 | {str(subclause_no)+'호' if subclause_no else ''} |\n"
        f"| 내용 | {내용_fmt} |\n"
        f"| 출처 | [국가법령정보센터 바로가기]({법령링크}) |\n"
    )

@app.get("/law", summary="법령 조문 조회")
@app.head("/law")
def get_law_clause(
    law_name: str = Query(None, example="학교폭력예방법"),
    article_no: str = Query(None, example="16"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    request: Request = None
):
    # 파라미터 없이 호출 시 안내 메시지
    if not law_name or not article_no:
        return {
            "error": "law_name, article_no 파라미터는 필수입니다. 예시: /law?law_name=학교폭력예방법&article_no=16"
        }

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
            return JSONResponse(content={"error": "법령 ID 조회 실패"}, status_code=404)
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
            return JSONResponse(content={"error": "해당 법령은 조회할 수 없습니다."}, status_code=403)
        내용 = extract_article(res.text, article_no, clause_no, subclause_no)
        law_url = make_law_url(law_name_full, article_no)
        markdown = make_markdown_table(law_name_full, article_no, clause_no, subclause_no, 내용, law_url)
        result = {
            "source": "api",
            "출처": "lawService",
            "법령명": law_name_full,
            "조문": f"제{article_no}조" if article_no else "",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": law_url,
            "markdown": markdown
        }
        log_entry["status"] = "success"
        log_entry["result"] = result
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        return JSONResponse(content=result)
    except Exception as e:
        log_entry["status"] = "error"
        log_entry["error"] = str(e)
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        print("🚨 API 에러:", e)
        return JSONResponse(content={"error": "API 호출 실패"}, status_code=500)

@app.get("/test-log", summary="최근 요청 로그 10건 조회")
@app.head("/test-log")
def test_log():
    return {"recent_logs": recent_logs[-10:]}
