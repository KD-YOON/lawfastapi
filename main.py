from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
import re
from difflib import get_close_matches

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API - 개선된 법령명 매칭")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "School LawBot API is live."}

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

def normalize_number(text: str) -> str:
    return ''.join(re.findall(r'\d+', text or ""))

def extract_subclause(text: str, sub_no: str):
    pattern = rf"{sub_no}\.\s*(.*?)(?=\n\d+\.|$)"
    match = re.search(pattern, text.replace("\r", "").replace("\n", "\n"), re.DOTALL)
    return match.group(1).strip() if match else None

@app.get("/law")
def get_clause(
    law_name: str = Query(...),
    article_no: str = Query(...),
    clause_no: str = Query(None),
    subclause_no: str = Query(None)
):
    if not API_KEY:
        return {"error": "API 키 없음"}

    original_name = law_name
    if law_name in ABBREVIATIONS:
        law_name = ABBREVIATIONS[law_name]

    article_norm = normalize_number(article_no)
    clause_norm = normalize_number(clause_no) if clause_no else None
    subclause_norm = normalize_number(subclause_no) if subclause_no else None

    try:
        res = requests.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": API_KEY, "target": "law", "query": law_name, "type": "XML"},
            timeout=10
        )
        res.raise_for_status()
        laws = ET.fromstring(res.content).findall("law")

        law_names = []
        id_map = {}
        for l in laws:
            full = (l.findtext("법령명") or "").replace("\u3000", "").strip()
            short = (l.findtext("법령약칭명") or "").replace("\u3000", "").strip()
            if full:
                law_names.append(full)
                id_map[full] = l.findtext("법령ID")
            if short:
                law_names.append(short)
                id_map[short] = l.findtext("법령ID")

        # 공백 제거 후 일치 확인
        def clean(s): return s.replace(" ", "").replace("\u3000", "").strip()
        matched_name = next((n for n in law_names if clean(n) == clean(law_name)), None)

        if not matched_name:
            match = get_close_matches(law_name.strip(), law_names, n=1, cutoff=0.6)
            matched_name = match[0] if match else None

        if not matched_name:
            return {
                "error": f"법령 '{law_name}' 찾을 수 없음",
                "suggestions": law_names
            }

        law_id = id_map.get(matched_name)
        if not law_id:
            return {"error": "법령 ID 없음"}

        detail = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={"OC": API_KEY, "target": "law", "lawId": law_id, "type": "XML"},
            timeout=10
        )
        detail.raise_for_status()
        root = ET.fromstring(detail.content)
        for article in root.findall(".//조문"):
            a_num = normalize_number(article.findtext("조문번호"))
            if a_num != article_norm:
                continue

            if not clause_no:
                return {
                    "법령명": matched_name,
                    "조문": article.findtext("조문번호"),
                    "내용": ET.tostring(article, encoding="unicode")
                }

            for clause in article.findall("항"):
                c_num = normalize_number(clause.findtext("항번호"))
                if c_num != clause_norm:
                    continue

                text = clause.findtext("항내용") or ""
                if not subclause_no:
                    return {
                        "법령명": matched_name,
                        "조문": article.findtext("조문번호"),
                        "항": clause.findtext("항번호"),
                        "내용": text
                    }

                ho_text = extract_subclause(text, subclause_no)
                return {
                    "법령명": matched_name,
                    "조문": article.findtext("조문번호"),
                    "항": clause.findtext("항번호"),
                    "호": subclause_no,
                    "내용": ho_text or "해당 호 없음"
                }

        return {"error": f"{matched_name}에서 제{article_no}조를 찾을 수 없습니다."}

    except Exception as e:
        return {"error": str(e)}
