from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import json

app = FastAPI(
    title="School LawBot API",
    description="단일 조문 API 기반 정확한 법령 조문 조회 서비스",
    version="3.0.0"
)

FALLBACK_FILE = "학교폭력예방 및 대책에 관한 법률.json"
OC_KEY = "dyun204"
DEBUG_MODE = True

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "개인정보보호법": "개인정보 보호법"
}

def resolve_full_law_name(law_name):
    return KNOWN_LAWS.get(law_name.strip(), law_name)

def normalize_law_name(law_name):
    return law_name.replace(" ", "").strip()

def get_law_id(law_name):
    normalized = normalize_law_name(law_name)
    try:
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        params = {
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "query": law_name
        }
        res = requests.get(search_url, params=params)
        res.raise_for_status()
        data = xmltodict.parse(res.text)

        law_entries = data.get("LawSearch", {}).get("laws", {}).get("law")
        if not law_entries:
            law_entries = data.get("LawSearch", {}).get("law", [])
        if isinstance(law_entries, dict):
            law_entries = [law_entries]

        for law in law_entries:
            if law.get("현행연혁코드") != "현행":
                continue
            for field in ["법령명한글", "법령약칭명", "법령명"]:
                if DEBUG_MODE:
                    print(f"🔍 비교 대상: {field} → {law.get(field)}")
                if normalize_law_name(law.get(field, "")) == normalized:
                    if DEBUG_MODE:
                        print(f"✅ 법령명 일치: {law.get(field)} → ID: {law.get('법령ID')}")
                    return law.get("법령ID")
        return None
    except Exception as e:
        if DEBUG_MODE:
            print("[lawId 자동 판별 오류]", e)
        return None

def extract_single_article(xml_text):
    try:
        data = xmltodict.parse(xml_text)
        if "조문" in data:
            조문 = data["조문"]
            return 조문.get("조문내용", "내용 없음")
        return "내용 없음"
    except Exception as e:
        if DEBUG_MODE:
            print(f"[Parsing Error] {e}")
        return "내용 없음"

@app.get("/law", summary="법령 조문 조회")
def get_law_clause(
    law_name: str = Query(..., example="학교폭력예방법"),
    article_no: str = Query(..., example="16")
):
    try:
        if DEBUG_MODE:
            print(f"📥 요청: {law_name} 제{article_no}조")

        law_name = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name)

        if not law_id:
            raise ValueError("법령 ID 조회 실패")

        detail_url = "https://www.law.go.kr/DRF/lawXmlDownload.do"
        params = {
            "OC": OC_KEY,
            "ID": law_id,
            "type": "XML",
            "article": article_no
        }

        res = requests.get(detail_url, params=params)
        res.raise_for_status()

        if DEBUG_MODE:
            print("[lawXmlDownload 응답 일부]:")
            print(res.text[:1000])

        내용 = extract_single_article(res.text)

        return JSONResponse(content={
            "source": "api",
            "출처": "lawXmlDownload.do",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/제{article_no}조"
        })

    except Exception as e:
        if DEBUG_MODE:
            print(f"🚨 예외 발생: {e}")
        return JSONResponse(content={
            "error": "API 호출 실패",
            "law_name": law_name,
            "article_no": article_no
        })
