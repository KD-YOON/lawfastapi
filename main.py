from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import json
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# fallback 파일 경로
FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"

# 유니코드 원 문자 정규화 (① → 1 등)
def normalize_clause_number(text):
    if not text:
        return None
    num_map = {
        "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
        "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9", "⑩": "10",
        "⑪": "11", "⑫": "12", "⑬": "13", "⑭": "14", "⑮": "15",
        "⑯": "16", "⑰": "17", "⑱": "18", "⑲": "19", "⑳": "20"
    }
    return "".join(num_map.get(ch, ch) for ch in text)

# fallback JSON 조회
def load_fallback(law_name, article_no, clause_no=None, subclause_no=None):
    try:
        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        조문_key = f"제{article_no}조"
        clause_no = normalize_clause_number(clause_no)
        subclause_no = normalize_clause_number(subclause_no)
        항_key = clause_no + "항" if clause_no else None
        호_key = subclause_no + "호" if subclause_no else None

        조문 = data["조문"].get(조문_key)
        if not 조문:
            return None

        항 = 조문.get("항", {}).get(항_key) if 항_key else None
        호 = 항.get("호", {}).get(호_key) if 항 and 호_key else None

        내용 = (
            호 or
            (항.get("내용") if 항 else None) or
            조문.get("조문")
        )

        return {
            "source": "fallback",
            "출처": "백업 데이터",
            "법령명": law_name,
            "조문": 조문_key,
            "항": 항_key or "",
            "호": 호_key or "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name)}/{조문_key}"
        }

    except Exception:
        return None

# law_name -> lawId 추출 함수 (XML 파싱)
def get_law_id(law_name):
    search_url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": "dyun204",
        "target": "law",
        "type": "XML",
        "query": law_name
    }
    response = requests.get(search_url, params=params)
    if response.status_code != 200:
        return None
    root = ET.fromstring(response.text)
    law_id_element = root.find("law/lawId")
    return law_id_element.text if law_id_element is not None else None

# 조문 추출 함수
def extract_clause_from_json(data, article_no, clause_no=None, subclause_no=None):
    try:
        article_key = f"제{article_no}조"
        clauses = data.get("조문", {}).get(article_key)
        if not clauses:
            return "해당 조문 없음"

        항 = clauses.get("항", {}).get(f"{clause_no}항") if clause_no else None
        호 = 항.get("호", {}).get(f"{subclause_no}호") if 항 and subclause_no else None
        return 호 or (항.get("내용") if 항 else None) or clauses.get("조문") or "내용 없음"
    except:
        return "내용 추출 오류"

@app.get("/")
def root():
    return {"message": "School LawBot API is running."}

@app.get("/law")
def get_law_clause(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="조문 번호"),
    clause_no: Optional[str] = Query(None, description="항 번호"),
    subclause_no: Optional[str] = Query(None, description="호 번호")
):
    try:
        law_id = get_law_id(law_name)
        if not law_id:
            raise ValueError("lawId 조회 실패")

        detail_url = "https://www.law.go.kr/DRF/lawService.do"
        params = {
            "OC": "dyun204",
            "target": "law",
            "type": "JSON",
            "lawId": law_id
        }
        res = requests.get(detail_url, params=params)
        res.raise_for_status()
        data = res.json()

        내용 = extract_clause_from_json(data, article_no, clause_no, subclause_no)

        return {
            "source": "api",
            "출처": "실시간 API",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "항": clause_no or "",
            "호": subclause_no or "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name)}/제{article_no}조"
        }

    except Exception as e:
        print(f"[API 호출 실패]: {e}")
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
