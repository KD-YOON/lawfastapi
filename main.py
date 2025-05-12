from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
from difflib import get_close_matches

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 약칭 + 유사도 기반 매칭")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 약칭 → 정식명 매핑
ABBREVIATIONS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "특수교육법": "장애인 등에 대한 특수교육법",
    "북한이탈주민법": "북한이탈주민의 보호 및 정착지원에 관한 법률",
    "아동복지법": "아동복지법",
    "교육기본법": "교육기본법",
    "초중등교육법": "초·중등교육법",
    "고등교육법": "고등교육법",
    "교원지위법": "교원의 지위 향상 및 교육활동 보호를 위한 특별법",
    "교직원징계령": "교육공무원 징계령",
    "공무원징계령": "국가공무원법 시행령",
    "성폭력처벌법": "성폭력범죄의 처벌 등에 관한 특례법",
    "청소년보호법": "청소년 보호법",
    "정보공개법": "공공기관의 정보공개에 관한 법률"
}

@app.get("/")
def root():
    return {
        "message": "📘 School LawBot API",
        "guide": "법령명을 약칭으로 입력해도 자동 매핑되며, 실시간 API로 조문/항을 검색합니다."
    }

# ✅ /law : 법령명 → ID
@app.get("/law")
def get_law(law_name: str = Query(...)):
    if not API_KEY:
        return {"error": "API 키가 없습니다.", "source": "fallback"}

    original = law_name
    if law_name in ABBREVIATIONS:
        law_name = ABBREVIATIONS[law_name]

    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        laws = ET.fromstring(res.content).findall("law")
        law_names = [l.findtext("lawName") for l in laws]
        match = get_close_matches(law_name, law_names, n=1, cutoff=0.8)

        if match:
            name = match[0]
            for law in laws:
                if law.findtext("lawName") == name:
                    return {
                        "law_name": name,
                        "law_id": law.findtext("lawId"),
                        "matched_from": original,
                        "source": "api"
                    }

        return {"error": f"'{original}' (→ '{law_name}') 법령 ID를 찾지 못했습니다.",
                "suggestions": law_names, "source": "fallback"}

    except Exception as e:
        return {"error": str(e), "source": "fallback"}

# ✅ /clause : 법령 조문 + 항
@app.get("/clause")
def get_clause(
    law_name: str = Query(...),
    article_no: str = Query(...),
    clause_no: str = Query(...)
):
    if not API_KEY:
        return {"error": "API 키가 없습니다.", "source": "fallback"}

    original = law_name
    if law_name in ABBREVIATIONS:
        law_name = ABBREVIATIONS[law_name]

    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        laws = ET.fromstring(res.content).findall("law")
        law_names = [l.findtext("lawName") for l in laws]
        match = get_close_matches(law_name, law_names, n=1, cutoff=0.8)

        if not match:
            return {"error": f"'{original}' (→ '{law_name}') 법령을 찾을 수 없습니다.",
                    "suggestions": law_names, "source": "fallback"}

        matched_name = match[0]
        law_id = None
        for law in laws:
            if law.findtext("lawName") == matched_name:
                law_id = law.findtext("lawId")
                break

        detail = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={"OC": API_KEY, "target": "law", "lawId": law_id, "type": "XML"},
            timeout=10
        )
        detail.raise_for_status()
        root = ET.fromstring(detail.content)

        for article in root.findall(".//조문"):
            if article.findtext("조문번호") == article_no:
                for clause in article.findall("항"):
                    if clause.findtext("항번호") == clause_no:
                        return {
                            "법령명": matched_name,
                            "조문": article_no,
                            "항": clause_no,
                            "내용": clause.findtext("항내용"),
                            "matched_from": original,
                            "source": "api"
                        }

        return {
            "error": f"{article_no} {clause_no} 항을 찾지 못했습니다.",
            "source": "fallback"
        }

    except Exception as e:
        return {"error": str(e), "source": "fallback"}
