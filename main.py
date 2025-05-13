from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
import re
from difflib import get_close_matches
from datetime import datetime
import traceback

load_dotenv()
API_KEY = os.getenv("LAW_API_KEY")
DEBUG = os.getenv("DEBUG", "False") == "True"

app = FastAPI(title="School LawBot API - 최신 법령 및 시행령 자동 구분")

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
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "특수교육법": "장애인 등에 대한 특수교육법",
    "아동복지법": "아동복지법",
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
        return {"error": "API 키 없음", "source": "fallback"}

    original_name = law_name
    law_name = ABBREVIATIONS.get(law_name, law_name)
    is_enforcement = "시행령" in law_name

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
        if DEBUG:
            print("📡 호출 URL:", res.url)

        laws = ET.fromstring(res.content).findall("law")

        latest_laws = {}
        for law in laws:
            full = (law.findtext("법령명") or "").replace("\u3000", "").strip()
            short = (law.findtext("법령약칭명") or "").replace("\u3000", "").strip()
            law_id = law.findtext("법령ID")
            pub_date = law.findtext("법령공포일자")

            try:
                pub_date_obj = datetime.strptime(pub_date, "%Y%m%d")
            except:
                continue

            for name in [full, short]:
                if name and (is_enforcement == ("시행령" in name)):
                    if name not in latest_laws or pub_date_obj > latest_laws[name]["date"]:
                        latest_laws[name] = {"id": law_id, "date": pub_date_obj}

        law_names = list(latest_laws.keys())
        id_map = {name: latest_laws[name]["id"] for name in law_names}

        def clean(s): return s.replace(" ", "").replace("\u3000", "").strip()
        match = get_close_matches(law_name.strip(), law_names, n=1, cutoff=0.6)
        matched_name = match[0] if match else next((n for n in law_names if clean(n) == clean(law_name)), None)

        if DEBUG:
            print("🧪 입력값:", original_name)
            print("🔍 보정:", law_name)
            print("📋 후보:", law_names)
            print("✅ 매칭:", matched_name)

        if not matched_name:
            return {
                "error": f"법령 '{law_name}' 찾을 수 없음",
                "suggestions": law_names[:10],
                "query_url": res.url,
                "source": "fallback"
            }

        law_id = id_map.get(matched_name)
        if not law_id:
            return {"error": "법령 ID 없음", "source": "fallback"}

        detail = requests.get(
            "https://www.law.go.kr/DRF/lawService.do",
            params={"OC": API_KEY, "target": "law", "lawId": law_id, "type": "XML"},
            timeout=10
        )
        detail.raise_for_status()
        root = ET.fromstring(detail.content)

        if DEBUG:
            print("📃 조문 목록:")
            for article in root.findall(".//조문"):
                print(" - 조문번호:", article.findtext("조문번호"))
                for clause in article.findall("항"):
                    print("   - 항번호:", clause.findtext("항번호"))
                    print("   - 항내용:", clause.findtext("항내용"))

        for article in root.findall(".//조문"):
            a_num = normalize_number(article.findtext("조문번호"))
            if a_num != article_norm:
                continue

            if not clause_no:
                return {
                    "법령명": matched_name,
                    "조문": article.findtext("조문번호"),
                    "내용": article.findtext("조문내용") or ET.tostring(article, encoding="unicode"),
                    "source": "api"
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
                        "내용": text or "내용 없음",
                        "source": "api"
                    }

                ho_text = extract_subclause(text, subclause_no)
                return {
                    "법령명": matched_name,
                    "조문": article.findtext("조문번호"),
                    "항": clause.findtext("항번호"),
                    "호": subclause_no,
                    "내용": ho_text or "해당 호 없음",
                    "source": "api"
                }

        return {
            "error": f"{matched_name}에서 제{article_no}조를 찾을 수 없습니다.",
            "source": "fallback"
        }

    except Exception as e:
        return {
            "error": str(e),
            "trace": traceback.format_exc(),
            "source": "fallback"
        }
