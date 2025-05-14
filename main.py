from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import json

app = FastAPI(
    title="School LawBot API",
    description="학교폭력예방 및 대책에 관한 법률 등을 실시간 API 또는 fallback JSON으로 제공하는 API입니다.",
    version="1.0.0"
)

FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"
OC_KEY = "dyun204"

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok"}

def normalize_law_name(law_name):
    return law_name.replace(" ", "").strip()

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
            "query": normalized
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
            if (
                law.get("현행연혁코드") == "현행" and
                normalize_law_name(law.get("법령명한글", "")) == normalized
            ):
                return law.get("법령ID")

        return None
    except Exception as e:
        print("[lawId 자동 판별 오류]", e)
        return None

def extract_clause_from_law_xml(xml_text, article_no, clause_no=None, subclause_no=None):
    try:
        data = xmltodict.parse(xml_text)

        if not isinstance(data, dict):
            raise ValueError("⚠️ XML 파싱 결과가 dict 아님")
        if "Law" not in data:
            raise ValueError("⚠️ 'Law' 키가 존재하지 않음")

        law = data["Law"]
        articles = law.get("article", [])
        if isinstance(articles, str):
            raise ValueError("⚠️ article이 문자열로 반환됨")
        if isinstance(articles, dict):
            articles = [articles]

        for article in articles:
            if not isinstance(article, dict):
                continue
            if article.get("ArticleTitle") == f"제{article_no}조":
                if clause_no:
                    clauses = article.get("Paragraph", [])
                    if isinstance(clauses, str):
                        raise ValueError("⚠️ Paragraph가 문자열로 반환됨")
                    if isinstance(clauses, dict):
                        clauses = [clauses]
                    for clause in clauses:
                        if not isinstance(clause, dict):
                            continue
                        if clause.get("ParagraphNum") == clause_no:
                            if subclause_no:
                                subclauses = clause.get("SubParagraph", [])
                                if isinstance(subclauses, str):
                                    raise ValueError("⚠️ SubParagraph가 문자열로 반환됨")
                                if isinstance(subclauses, dict):
                                    subclauses = [subclauses]
                                for sub in subclauses:
                                    if not isinstance(sub, dict):
                                        continue
                                    if sub.get("SubParagraphNum") == subclause_no:
                                        content = sub.get("SubParagraphContent")
                                        if isinstance(content, str):
                                            return content
                                        elif isinstance(content, dict):
                                            return content.get("#text", "내용 없음")
                            return clause.get("ParagraphContent", "내용 없음")
                return article.get("ArticleContent", "내용 없음")

        return "내용 없음"

    except Exception as e:
        print(f"[Parsing Error] {e}")
        return "내용 없음"

@app.get(
    "/law",
    summary="법령 조문 조회",
    description="실시간 API 또는 fallback JSON을 통해 법령의 특정 조문, 항, 호의 내용을 조회합니다."
)
def get_law_clause(
    law_name: str = Query(..., description="법령명 (예: 학교폭력예방 및 대책에 관한 법률)", example="학교폭력예방 및 대책에 관한 법률"),
    article_no: str = Query(..., description="조문 번호 (예: 17)", example="17"),
    clause_no: Optional[str] = Query(None, description="항 번호 (예: 1)", example="1"),
    subclause_no: Optional[str] = Query(None, description="호 번호 (예: 2)", example="2")
):
    try:
        print(f"📥 요청 수신됨: {law_name} {article_no} {clause_no} {subclause_no}")
        law_id = get_law_id(law_name)
        print(f"🔍 law_id 탐색 결과: {law_id}")

        if not law_id:
            raise ValueError("lawId 조회 실패")

        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        params = {
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "lawId": law_id
        }
        res = requests.get(detail_url, params=params)
        res.raise_for_status()

        내용 = extract_clause_from_law_xml(res.text, article_no, clause_no, subclause_no)
        print(f"✅ 추출된 내용: {내용[:100]}")

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
        print(f"🚨 예외 발생: {e}")
        fallback = load_fallback(law_name, article_no, clause_no, subclause_no)
        if fallback:
            return fallback
        else:
            return JSONResponse(content={
                "error": "API 호출 실패 및 fallback 없음",
                "law_name": law_name,
                "article_no": article_no,
                "clause_no": clause_no or "",
                "subclause_no": subclause_no or ""
            })
