from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
import difflib

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 실시간 우선 + fallback 안내")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "message": "📘 School LawBot API",
        "guide": (
            "🔐 실시간 법령 정보를 불러오려면 GPT 상단의 '허용하기' 버튼을 눌러야 합니다.\n"
            "모든 응답은 외부 API 연결을 우선 시도하고, 실패할 경우 내부 요약으로 대체됩니다."
        ),
        "example": "/clause?law_name=학교폭력예방법&article_no=제16조&clause_no=제1항"
    }

@app.get("/law")
def get_law(law_name: str = Query(..., description="법령명")):
    if not API_KEY:
        return {"error": "API 키가 없습니다", "source": "fallback"}

    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        laws = ET.fromstring(res.content).findall("law")

        for law in laws:
            if law.findtext("lawName") == law_name:
                return {
                    "law_name": law.findtext("lawName"),
                    "law_id": law.findtext("lawId"),
                    "source": "api"
                }

        return {
            "error": f"'{law_name}'의 정확한 법령 ID를 찾을 수 없습니다.",
            "suggestions": [l.findtext("lawName") for l in laws],
            "source": "fallback"
        }

    except Exception as e:
        return {"error": str(e), "source": "fallback"}

@app.get("/clause")
def get_clause(
    law_name: str = Query(...),
    article_no: str = Query(...),
    clause_no: str = Query(...)
):
    if not API_KEY:
        return {"error": "API 키가 없습니다", "source": "fallback"}

    try:
        # Step 1: lawId 정확히 찾기
        search_res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        search_res.raise_for_status()
        laws = ET.fromstring(search_res.content).findall("law")
        law_id = None
        for law in laws:
            if law.findtext("lawName") == law_name:
                law_id = law.findtext("lawId")
                break

        if not law_id:
            return {
                "error": f"'{law_name}' 법령 ID를 찾을 수 없습니다.",
                "suggestions": [l.findtext("lawName") for l in laws],
                "source": "fallback"
            }

        # Step 2: 전체 조문 조회
        law_res = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={"OC": API_KEY, "target": "law", "lawId": law_id, "type": "XML"},
            timeout=10
        )
        law_res.raise_for_status()
        root = ET.fromstring(law_res.content)

        articles = root.findall(".//조문")
        for article in articles:
            if article.findtext("조문번호") == article_no:
                clauses = article.findall("항")
                for clause in clauses:
                    if clause.findtext("항번호") == clause_no:
                        return {
                            "법령명": law_name,
                            "조문번호": article_no,
                            "항번호": clause_no,
                            "내용": clause.findtext("항내용"),
                            "source": "api"
                        }

                clause_numbers = [c.findtext("항번호") for c in clauses if c.findtext("항번호")]
                suggestion = difflib.get_close_matches(clause_no, clause_numbers, n=1, cutoff=0.5)
                return {
                    "error": f"{article_no} 내에 '{clause_no}' 항이 없습니다.",
                    "suggestion": suggestion[0] if suggestion else None,
                    "available_clauses": clause_numbers,
                    "source": "fallback"
                }

        return {
            "error": f"'{article_no}' 조문을 찾을 수 없습니다.",
            "source": "fallback"
        }

    except Exception as e:
        return {
            "error": f"API 호출 중 오류 발생: {str(e)}",
            "source": "fallback"
        }
