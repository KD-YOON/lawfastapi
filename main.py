from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
import difflib

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 조문+항 정확 응답 + API 허용 안내")

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
            "🔐 이 API는 국가법령정보센터와 실시간으로 연결됩니다.\n"
            "ChatGPT 사용 시 화면 상단에 '허용하기' 또는 '항상 허용하기' 버튼이 뜨면 반드시 눌러주세요.\n"
            "버튼을 누르지 않으면 GPT가 외부 API를 호출할 수 없습니다."
        ),
        "examples": {
            "조문조회": "/article?law_name=학교폭력예방 및 대책에 관한 법률&article_no=제16조",
            "항조회": "/clause?law_name=학교폭력예방 및 대책에 관한 법률&article_no=제16조&clause_no=제3항"
        }
    }

@app.get("/law")
def get_law(law_name: str = Query(..., description="법령명 입력")):
    if not API_KEY:
        return {
            "error": "API 키가 누락되었습니다.",
            "tip": "GPT 상단에 '허용하기' 버튼이 보이면 눌러 주세요."
        }

    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        root = ET.fromstring(res.content)
        law_id = root.findtext("law/lawId")
        law_title = root.findtext("law/lawName")

        if not law_id:
            return {"error": f"'{law_name}'에 대한 법령 ID를 찾을 수 없습니다."}

        return {"law_name": law_title, "law_id": law_id}

    except Exception as e:
        return {
            "error": str(e),
            "tip": "📢 외부 API 연결이 실패했습니다. GPT 상단의 '허용하기' 버튼을 눌렀는지 확인해 주세요."
        }

@app.get("/clause")
def get_clause(
    law_name: str = Query(...),
    article_no: str = Query(...),
    clause_no: str = Query(...)
):
    if not API_KEY:
        return {"error": "API 키가 없습니다. .env 또는 환경변수 설정 필요"}

    try:
        # 1. 법령 ID 조회
        search_res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        search_res.raise_for_status()
        law_id = ET.fromstring(search_res.content).findtext("law/lawId")

        if not law_id:
            return {"error": f"'{law_name}'의 법령 ID를 찾을 수 없습니다."}

        # 2. 조문 전체 불러오기
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
                clause_numbers = [c.findtext("항번호") for c in clauses if c.findtext("항번호")]

                for clause in clauses:
                    if clause.findtext("항번호") == clause_no:
                        return {
                            "법령명": law_name,
                            "조문번호": article_no,
                            "항번호": clause_no,
                            "내용": clause.findtext("항내용")
                        }

                suggestion = difflib.get_close_matches(clause_no, clause_numbers, n=1, cutoff=0.5)
                return {
                    "error": f"{article_no} 안에 '{clause_no}' 항이 없습니다.",
                    "suggestion": suggestion[0] if suggestion else None,
                    "available_clauses": clause_numbers
                }

        article_list = [a.findtext("조문번호") for a in articles if a.findtext("조문번호")]
        suggestion = difflib.get_close_matches(article_no, article_list, n=1, cutoff=0.5)
        return {
            "error": f"'{article_no}' 조문을 찾을 수 없습니다.",
            "suggestion": suggestion[0] if suggestion else None,
            "available_articles": article_list
        }

    except Exception as e:
        return {
            "error": f"조문 또는 항 조회 중 오류: {str(e)}",
            "tip": "📢 GPT 상단의 '허용하기' 버튼을 눌러야 외부 API 호출이 정상적으로 작동합니다."
        }
