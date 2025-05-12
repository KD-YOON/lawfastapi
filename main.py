from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv

load_dotenv()  # .env 파일에서 환경변수 로드

API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI(title="School LawBot API")

# GPT 외부 연결 허용 (CORS 허용 설정)
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
        "message": "📘 School LawBot API (국가법령정보센터 연동 중)",
        "guide": (
            "🔐 외부 API를 사용하므로 GPT 화면 상단에 '허용하기' 또는 '항상 허용하기' 버튼이 표시됩니다.\n"
            "이 버튼을 눌러야 실제 법령 데이터가 정상적으로 불러와집니다."
        ),
        "example": "/law?law_name=학교폭력예방 및 대책에 관한 법률"
    }

@app.get("/law")
def get_law(law_name: str = Query(..., description="법령명을 입력하세요")):
    if not API_KEY:
        return {
            "error": "API 키가 설정되지 않았습니다. .env 또는 Render 환경변수에 LAW_API_KEY를 입력해 주세요.",
            "tip": "🔐 GPT 화면 상단의 '허용하기' 버튼이 떠 있다면 꼭 눌러 주세요!"
        }

    url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": API_KEY,
        "target": "law",
        "query": law_name,
        "type": "XML"
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()

        root = ET.fromstring(res.content)
        law = root.find("law")

        if law is None:
            return {
                "error": f"'{law_name}'에 해당하는 법령을 찾을 수 없습니다.",
                "tip": "법령명이 정확한지 확인해 주세요."
            }

        law_id = law.findtext("lawId")
        law_title = law.findtext("lawName")

        if not law_id:
            return {
                "error": f"'{law_name}'의 lawId를 찾을 수 없습니다.",
                "tip": "법령명이 정확하지만 법령 ID가 누락되어 있을 수 있습니다."
            }

        return {
            "law_name": law_title,
            "law_id": law_id
        }

    except requests.exceptions.Timeout:
        return {"error": "국가법령정보센터 응답 시간 초과"}
    except ET.ParseError:
        return {"error": "XML 파싱 오류 (mismatched tag 등)"}
    except Exception as e:
        return {"error": str(e)}
