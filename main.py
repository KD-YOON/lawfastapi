from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
import difflib

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 실시간 조문 + 오타 방지")

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
        "message": "📘 School LawBot API (국가법령정보센터 실시간 연동)",
        "guide": (
            "🔐 GPT에서 상단 '허용하기' 또는 '항상 허용하기'를 누르지 않으면 법령 연결이 되지 않습니다."
        ),
        "example": "/article?law_name=학교폭력예방 및 대책에 관한 법률&article_no=제16조"
    }

@app.get("/law")
def get_law(law_name: str = Query(..., description="법령명을 정확하게 입력하세요")):
    if not API_KEY:
        return {"error": "API 키 누락 - Render의 환경변수 또는 .env 파일 확인 필요"}

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
            return {"error": f"'{law_name}'에 대한 lawId를 찾을 수 없습니다."}

        return {"law_name": law_title, "law_id": law_id}

    except Exception as e:
        return {"error": str(e)}

@app.get("/article")
def get_article(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="예: 제16조")
):
    if not API_KEY:
        return {"error": "API 키가 누락되어 있습니다."}

    # 1단계: lawId 검색
    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        root = ET.fromstring(res.content)
        law_id = root.findtext("law/lawId")

        if not law_id:
            return {"error": f"'{law_name}'에 해당하는 법령을 찾을 수 없습니다."}

    except Exception as e:
        return {"error": f"법령 ID 검색 중 오류: {str(e)}"}

    # 2단계: 조문 전체 조회
    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={"OC": API_KEY, "target": "law", "type": "XML", "lawId": law_id},
            timeout=10
        )
        res.raise_for_status()
        law_xml = ET.fromstring(res.content)

        articles = law_xml.findall(".//조문")
        all_numbers = [a.findtext("조문번호") for a in articles if a.findtext("조문번호")]

        # 3단계: 정확한 조문 찾기
        for article in articles:
            if article.findtext("조문번호") == article_no:
                return {
                    "법령명": law_name,
                    "조문번호": article_no,
                    "조문제목": article.findtext("조문제목"),
                    "조문내용": article.findtext("조문내용")
                }

        # 4단계: 유사 조문 추천
        suggestion = difflib.get_close_matches(article_no, all_numbers, n=1, cutoff=0.5)
        return {
            "error": f"'{article_no}' 조문은 존재하지 않습니다.",
            "suggestion": suggestion[0] if suggestion else None,
            "available_articles": all_numbers
        }

    except Exception as e:
        return {"error": f"조문 파싱 중 오류: {str(e)}"}
