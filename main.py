from fastapi import FastAPI, Query
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import json

app = FastAPI(
    title="School LawBot API",
    description="""
School LawBot API는 외부 법령 검색 기능을 위해 국가법령정보센터 API를 사용합니다. 
이 서비스는 사용자의 개인정보를 수집하지 않으며, 법령 조문 검색만을 수행합니다.

🔒 개인정보보호방침: https://kd-yoon.github.io/privacy-policy
📄 이용약관: https://kd-yoon.github.io/terms
""",
    version="1.0.0"
)

FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"

# fallback 조회
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

        return {
            "source": "fallback",
            "출처": "백업 데이터",
            "법령명": law_name,
            "조문": article_key,
            "항": clause_key or "",
            "호": subclause_key or "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name)}/{article_key}"
        }

    except Exception as e:
        print(f"[Fallback Error] {e}")
        return None

def get_law_id(law_name):
    try:
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {
            "OC": "dyun204",
            "target": "law",
            "type": "XML",
            "query": law_name
        }
        res = requests.get(search_url, params=params)
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        law_entry = data.get("LawSearch", {}).get("law")

        print("lawSearch 결과 법령명 목록:")

        if isinstance(law_entry, list):
            for law in law_entry:
                print(" -", law.get("법령명"))
                if law_name in law.get("법령명", ""):
                    return law.get("lawId")
        elif isinstance(law_entry, dict):
            print(" -", law_entry.get("법령명"))
            if law_name in law_entry.get("법령명", ""):
                return law_entry.get("lawId")

        return None
    except Exception as e:
        print("[lawId 조회 오류]", e)
        return None
    except Exception as e:
        print("[lawId 조회 오류]", e)
        return None

def extract_clause_from_law_xml(xml_text, article_no, clause_no=None, subclause_no=None):
    try:
        data = xmltodict.parse(xml_text)
        articles = data.get("Law", {}).get("article", [])
        if isinstance(articles, dict):
            articles = [articles]

        for article in articles:
            if article.get("ArticleTitle") == f"제{article_no}조":
                if clause_no:
                    clauses = article.get("Paragraph", [])
                    if isinstance(clauses, dict):
                        clauses = [clauses]
                    for clause in clauses:
                        if clause.get("ParagraphNum") == clause_no:
                            if subclause_no:
                                subclauses = clause.get("SubParagraph", [])
                                if isinstance(subclauses, dict):
                                    subclauses = [subclauses]
                                for sub in subclauses:
                                    if sub.get("SubParagraphNum") == subclause_no:
                                        return sub.get("SubParagraphContent")
                            return clause.get("ParagraphContent")
                return article.get("ArticleContent")
        return "내용 없음"
    except Exception as e:
        print("[XML 파싱 오류]", e)
        return "내용 추출 오류"

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return {"message": "pong"}

@app.get("/law")
def get_law_clause(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="조문 번호"),
    clause_no: Optional[str] = Query(None, description="항 번호"),
    subclause_no: Optional[str] = Query(None, description="호 번호")
):
    try:
        print("📥 요청 수신됨:", law_name, article_no, clause_no, subclause_no)
        law_id = get_law_id(law_name)
        print(f"🔍 law_id: {law_id}")
        if not law_id:
            raise ValueError("lawId 조회 실패")

        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        params = {
            "OC": "dyun204",
            "target": "law",
            "type": "XML",
            "lawId": law_id
        }
        res = requests.get(detail_url, params=params)
        res.raise_for_status()
        print(f"📄 lawService 응답 앞부분:\n{res.text[:500]}")

        내용 = extract_clause_from_law_xml(res.text, article_no, clause_no, subclause_no)
        print(f"✅ 최종 추출된 내용:\n{내용}")

        return {
            "source": "api",
            "출처": "실시간 API",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name)}/제{article_no}조"
        }

    except Exception as e:
        print("🚨 [예외 발생]", e)
        fallback = load_fallback(law_name, article_no, clause_no, subclause_no)
        if fallback:
            return fallback
        else:
            return {
                "error": "API 호출 실패 및 fallback 없음",
                "law_name": law_name,
                "article_no": article_no,
                "clause_no": clause_no or "",
                "subclause_no": subclause_no or ""
            }
