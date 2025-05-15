from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import json

app = FastAPI(
    title="School LawBot API",
    description="법령정보 DRF API 기반 조문, 항, 호 조회 서비스",
    version="3.2.1"
)

FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"
OC_KEY = "dyun204"
DEBUG_MODE = True

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "개인정보보호법": "개인정보 보호법"
}

def resolve_full_law_name(law_name):
    return KNOWN_LAWS.get(law_name.strip(), law_name)

def normalize_law_name(law_name):
    return law_name.replace(" ", "").strip()

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
                if normalize_law_name(law.get(field, "")) == normalized:
                    if DEBUG_MODE:
                        print(f"✅ 법령명 일치: {law.get(field)} → ID: {law.get('법령ID')}")
                    return law.get("법령ID")
        return None
    except Exception as e:
        if DEBUG_MODE:
            print("[lawId 오류]", e)
        return None

def extract_article(xml_text, article_no: str, clause_no: Optional[str], subclause_no: Optional[str]):
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("Law", {})
        articles = law.get("article")

        if isinstance(articles, dict):
            articles = [articles]

        for article in articles:
            if article.get("ArticleTitle") != f"제{article_no}조":
                continue

            # clause 요청 시
            if clause_no:
                clauses = article.get("Paragraph")
                if not clauses:
                    return "요청한 항이 존재하지 않습니다."
                if isinstance(clauses, dict):
                    clauses = [clauses]

                for clause in clauses:
                    if clause.get("ParagraphNum") == clause_no:
                        if subclause_no:
                            subclauses = clause.get("SubParagraph")
                            if not subclauses:
                                return "요청한 호가 존재하지 않습니다."
                            if isinstance(subclauses, dict):
                                subclauses = [subclauses]
                            for sub in subclauses:
                                if sub.get("SubParagraphNum") == subclause_no:
                                    return sub.get("SubParagraphContent", "내용 없음")
                            return "요청한 호가 존재하지 않습니다."
                        return clause.get("ParagraphContent", "내용 없음")

                return "요청한 항이 존재하지 않습니다."

            # clause_no 없을 때
            return article.get("ArticleContent", "내용 없음")

        return "요청한 조문을 찾을 수 없습니다."
    except Exception as e:
        if DEBUG_MODE:
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
        if DEBUG_MODE:
            print(f"📥 요청: {law_name} 제{article_no}조 {clause_no or ''}항 {subclause_no or ''}호")

        law_name = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name)
        if not law_id:
            raise ValueError("법령 ID를 찾을 수 없습니다.")

        res = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={
                "OC": OC_KEY,
                "target": "law",
                "type": "XML",
                "ID": law_id
            }
        )
        res.raise_for_status()

        if DEBUG_MODE:
            print("[lawService 응답 일부]:")
            print(res.text[:1000])

        내용 = extract_article(res.text, article_no, clause_no, subclause_no)

        return JSONResponse(content={
            "source": "api",
            "출처": "lawService.do",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/제{article_no}조"
        })

    except Exception as e:
        if DEBUG_MODE:
            print(f"🚨 예외: {e}")
        return JSONResponse(content={
            "error": "API 호출 실패",
            "law_name": law_name,
            "article_no": article_no,
            "clause_no": clause_no or "",
            "subclause_no": subclause_no or ""
        })
