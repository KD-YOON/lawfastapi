from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import json

app = FastAPI(
    title="School LawBot API",
    description="학교폭력예방법 등 실시간 API 또는 fallback JSON을 통한 조문 조회 서비스",
    version="1.4.1"
)

FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"
OC_KEY = "dyun204"

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "개인정보보호법": "개인정보 보호법"
}

def resolve_full_law_name(law_name):
    return KNOWN_LAWS.get(law_name.strip(), law_name)

def normalize_law_name(law_name):
    return law_name.replace(" ", "").strip()

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok"}

@app.get("/")
def home():
    return {"message": "School LawBot API is live. Use /docs to test the endpoints."}

def load_fallback(law_name, article_no, clause_no=None, subclause_no=None):
    try:
        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        article_key = f"제{article_no}조"
        clause_key = f"{clause_no}항" if clause_no else None
        subclause_key = f"{subclause_no}호" if subclause_no else None

        article = data.get("조문", {}).get(article_key)
        if not article:
            return None

        clause = article.get("항", {}).get(clause_key) if clause_key else None
        subclause = clause.get("호", {}).get(subclause_key) if clause and subclause_key else None

        내용 = (
            subclause or
            (clause.get("내용") if clause else None) or
            article.get("조문")
        )

        return JSONResponse(content={
            "source": "fallback",
            "출처": "백업 데이터",
            "법령명": law_name,
            "조문": article_key,
            "항": clause_key or "",
            "호": subclause_key or "",
            "내용": 내용 or "내용이 없습니다.",
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/{article_key}"
        })
    except Exception as e:
        print(f"[Fallback Error] {e}")
        return None

def get_law_id(law_name):
    normalized = normalize_law_name(law_name)
    try:
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "query": law_name
        }
        res = requests.get(search_url, params=params)
        res.raise_for_status()
        data = xmltodict.parse(res.text)

        law_entries = data.get("LawSearch", {}).get("laws", {}).get("law")
        if not law_entries:
            law_entries = data.get("LawSearch", {}).get("law", [])
        if isinstance(law_entries, dict):
            law_entries = [law_entries]

        for law in law_entries:
            if law.get("현행연혁코드") != "현행":
                continue
            for field in ["법령명한글", "법령약칭명", "법령명"]:
                print(f"🔍 비교 대상: {field} → {law.get(field)}")
                if normalize_law_name(law.get(field, "")) == normalized:
                    print(f"✅ 법령명 일치: {law.get(field)} → ID: {law.get('법령ID')}")
                    return law.get("법령ID")
        print("❌ 일치하는 법령명 없음")
        return None
    except Exception as e:
        print("[lawId 자동 판별 오류]", e)
        return None

# ✅ 시행예정 조문 필터링 및 XML 원문 출력 추가
def extract_clause_from_law_xml(xml_text, article_no, clause_no=None, subclause_no=None):
    try:
        print("📦 lawService 응답 원문 일부 ↓↓↓")
        print(xml_text[:1000])  # XML 내용 일부 출력

        data = xmltodict.parse(xml_text)

        if "조문시행일자조회결과" in data:
            시행일 = data["조문시행일자조회결과"].get("조문시행일자", "시행 예정일 정보 없음")
            안내문 = f"[현행법 아님] 이 조문은 아직 시행되지 않았습니다. 시행일자: {시행일}"
            print(f"🕓 시행예정 조문 → 거부: {안내문}")
            return 안내문

        if "LawService" in data or "Law" not in data:
            raise ValueError("법령 없음 또는 구조 이상")

        law = data.get("Law")
        articles = law.get("article")
        if isinstance(articles, dict): articles = [articles]

        for article in articles:
            if article.get("ArticleTitle") != f"제{article_no}조":
                continue

            if clause_no and "Paragraph" in article:
                clauses = article.get("Paragraph")
                if isinstance(clauses, dict): clauses = [clauses]
                for clause in clauses:
                    if clause.get("ParagraphNum") != clause_no:
                        continue
                    if subclause_no and "SubParagraph" in clause:
                        subclauses = clause.get("SubParagraph")
                        if isinstance(subclauses, dict): subclauses = [subclauses]
                        for sub in subclauses:
                            if sub.get("SubParagraphNum") == subclause_no:
                                return sub.get("SubParagraphContent", "내용 없음")
                    return clause.get("ParagraphContent", "내용 없음")

            if "ArticleContent" in article:
                return article.get("ArticleContent", "내용 없음")

        return "내용 없음"
    except Exception as e:
        print(f"[Parsing Error] {e}")
        return "내용 없음"

@app.get("/law", summary="법령 조문 조회")
def get_law_clause(
    law_name: str = Query(..., example="학교폭력예방법"),
    article_no: str = Query(..., example="16"),
    clause_no: Optional[str] = Query(None, example="1"),
    subclause_no: Optional[str] = Query(None, example="2")
):
    try:
        print(f"📥 요청: {law_name} 제{article_no}조 {clause_no or ''}항 {subclause_no or ''}호")
        law_name = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name)
        print(f"🔍 law_id 결과: {law_id}")

        if not law_id:
            raise ValueError("lawId 조회 실패")

        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        params = {
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "ID": law_id
        }
        res = requests.get(detail_url, params=params)

        print("[lawService 응답 status_code]", res.status_code)
        res.raise_for_status()
        print("[lawService 응답 구조 디버깅]", res.text[:500])

        내용 = extract_clause_from_law_xml(res.text, article_no, clause_no, subclause_no)
        print(f"✅ 최종 내용: {내용[:80]}...")

        return JSONResponse(content={
            "source": "api",
            "출처": "실시간 API",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/제{article_no}조"
        })

    except Exception as e:
        print(f"🚨 API 예외: {e}")
        fallback = load_fallback(law_name, article_no, clause_no, subclause_no)
        return fallback or JSONResponse(content={
            "error": "API 호출 실패 및 fallback 없음",
            "law_name": law_name,
            "article_no": article_no,
            "clause_no": clause_no or "",
            "subclause_no": subclause_no or ""
        })
