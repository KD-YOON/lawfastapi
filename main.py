from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
from difflib import get_close_matches
import re

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 약칭 + 유사도 + 전처리")

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

def normalize_number(text, target):
    # '조' 또는 '항'에 따라 형식 지정
    num = ''.join(re.findall(r'\d+', text))
    if not num:
        return text
    return f"제{int(num)}{'조' if target == 'article' else '항'}"

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

    article_no_norm = normalize_number(article_no, "article")
    clause_no_norm = normalize_number(clause_no, "clause")

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

        articles = root.findall(".//조문")
        article_nums = [a.findtext("조문번호") for a in articles if a.findtext("조문번호")]
        article_match = get_close_matches(article_no_norm, article_nums, n=1, cutoff=0.6)

        if article_match:
            for article in articles:
                if article.findtext("조문번호") == article_match[0]:
                    clauses = article.findall("항")
                    clause_nums = [c.findtext("항번호") for c in clauses if c.findtext("항번호")]
                    clause_match = get_close_matches(clause_no_norm, clause_nums, n=1, cutoff=0.6)
                    if clause_match:
                        for clause in clauses:
                            if clause.findtext("항번호") == clause_match[0]:
                                return {
                                    "법령명": matched_name,
                                    "조문": article_match[0],
                                    "항": clause_match[0],
                                    "내용": clause.findtext("항내용"),
                                    "matched_from": original,
                                    "source": "api"
                                }

        return {"error": "조문 또는 항을 찾지 못했습니다.", "source": "fallback"}

    except Exception as e:
        return {"error": str(e), "source": "fallback"}
