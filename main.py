from fastapi import FastAPI, Query
from typing import Optional
from urllib.parse import unquote, quote
import json
import requests
import xmltodict

app = FastAPI()

API_KEY = "dyun204"
API_BASE_URL = "https://www.law.go.kr/DRF/lawService.do"

# 로컬 JSON 파일 로딩
with open("학교폭력예방 및 대책에 관한 법률.json", "r", encoding="utf-8") as f:
    law_data = json.load(f)

with open("2. 학교폭력예방 및 대책에 관한 법률 시행령.json", "r", encoding="utf-8") as f:
    regulation_data = json.load(f)

law_name_map = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "학교폭력예방 및 대책에 관한 법률": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방 및 대책에 관한 법률 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "특수교육법": "장애인 등에 대한 특수교육법",
    "북한이탈주민지원법": "북한이탈주민의 보호 및 정착지원에 관한 법률"
}

@app.get("/")
def root():
    return {"message": "School LawBot API 정상 작동 중"}

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/laws")
def get_laws():
    return list(law_name_map.keys())

def normalize_clause_key(clause_dict, clause_no):
    # 항 키(①항 등)와 숫자형(1항) 매칭
    for key in clause_dict.keys():
        if clause_no in key or key.strip("항") == clause_no:
            return key
    return None

@app.get("/law")
def get_law(
    law_name: str = Query(..., description="법령명 또는 약칭"),
    article_no: str = Query(..., description="조문 번호"),
    clause_no: Optional[str] = Query(None, description="항 번호"),
    subclause_no: Optional[str] = Query(None, description="호 번호")
):
    decoded_law_name = unquote(law_name)
    standard_name = law_name_map.get(decoded_law_name, decoded_law_name)

    # 실시간 API 호출 시도
    try:
        response = requests.get(API_BASE_URL, params={
            "OC": API_KEY,
            "target": "law",
            "ID": "1863677",  # 예시 ID
            "type": "XML"
        }, timeout=5)

        if response.status_code == 200:
            parsed = xmltodict.parse(response.text)
            law_info = parsed.get("Law", {})
            if law_info and "Article" in law_info:
                return {
                    "source": "api",
                    "raw": law_info,
                    "법령링크": f"https://www.law.go.kr/법령/{quote(standard_name)}/제{article_no}조"
                }
    except Exception:
        pass  # API 실패 시 fallback

    # fallback
    if standard_name == law_data.get("법령명"):
        data = law_data
    elif standard_name == regulation_data.get("법령명"):
        data = regulation_data
    else:
        return {
            "source": "fallback",
            "error": f"법령 '{decoded_law_name}'을 찾을 수 없음",
            "law_name": decoded_law_name
        }

    articles = data.get("조문", {})
    article = articles.get(f"제{article_no}조")
    if not article:
        return {"source": "fallback", "error": f"제{article_no}조를 찾을 수 없습니다."}

    if clause_no:
        clause_dict = article.get("항", {})
        clause_key = normalize_clause_key(clause_dict, clause_no)
        if clause_key and clause_key in clause_dict:
            clause = clause_dict[clause_key]
            result = {
                "source": "fallback",
                "법령명": standard_name,
                "조문": f"제{article_no}조",
                "항": clause_key,
                "내용": clause.get("내용"),
                "법령링크": f"https://www.law.go.kr/법령/{quote(standard_name)}/제{article_no}조"
            }
            if subclause_no:
                subclause = clause.get("호", {}).get(f"{subclause_no}호") or clause.get("호", {}).get(f"{subclause_no}.")
                result["호"] = subclause or "해당 호 없음"
            else:
                result["호"] = clause.get("호")
            return result
        else:
            return {"source": "fallback", "error": f"제{article_no}조 제{clause_no}항을 찾을 수 없습니다."}
    else:
        return {
            "source": "fallback",
            "법령명": standard_name,
            "조문": f"제{article_no}조",
            "조문명": article.get("조문명"),
            "조문": article.get("조문"),
            "항": article.get("항"),
            "법령링크": f"https://www.law.go.kr/법령/{quote(standard_name)}/제{article_no}조"
        }
