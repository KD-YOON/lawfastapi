from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv

load_dotenv()  # .env 파일에서 환경변수 로드

API_KEY = os.getenv("LAW_API_KEY")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "School LawBot API (국가법령정보센터 연결)"}

@app.get("/law")
def get_law(law_name: str = "학교폭력예방 및 대책에 관한 법률"):
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
        return {
            "title": root.findtext("law/lawName"),
            "id": root.findtext("law/lawId")
        }
    except Exception as e:
        return {"error": str(e)}
