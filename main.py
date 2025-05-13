
from fastapi import FastAPI, Query
from typing import Optional
from urllib.parse import unquote
import json

app = FastAPI()

# JSON 파일 로딩
with open("학교폭력예방 및 대책에 관한 법률.json", "r", encoding="utf-8") as f:
    law_data = json.load(f)

with open("학교폭력예방 및 대책에 관한 법률 시행령.json", "r", encoding="utf-8") as f:
    regulation_data = json.load(f)

# 약칭 → 정식 법령명 매핑
law_name_map = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "학교폭력예방 및 대책에 관한 법률": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방 및 대책에 관한 법률 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "특수교육법": "장애인 등에 대한 특수교육법",
    "북한이탈주민지원법": "북한이탈주민의 보호 및 정착지원에 관한 법률"
}

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/law")
def get_law(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="조문번호"),
    clause_no: Optional[str] = Query(None, description="항 번호")
):
    decoded_law_name = unquote(law_name)
    standard_name = law_name_map.get(decoded_law_name, decoded_law_name)

    # 해당 법령 데이터 선택
    if standard_name == law_data.get("법령명"):
        data = law_data
    elif standard_name == regulation_data.get("법령명"):
        data = regulation_data
    else:
        return {
            "error": f"법령 '{decoded_law_name}'을 찾을 수 없음",
            "law_name": decoded_law_name,
            "available": [law_data.get("법령명"), regulation_data.get("법령명")]
        }

    articles = data.get("조문", {})
    article = articles.get(f"제{article_no}조")
    if not article:
        return {"error": f"제{article_no}조를 찾을 수 없습니다."}

    if clause_no:
        clause = article.get("항", {}).get(f"{clause_no}항")
        if clause:
            return {
                "source": "api",
                "law_name": standard_name,
                "article": f"제{article_no}조",
                "clause": f"{clause_no}항",
                "조문명": article.get("조문명"),
                "조문": article.get("조문"),
                "내용": clause.get("내용"),
                "호": clause.get("호")
            }
        else:
            return {"error": f"제{article_no}조 제{clause_no}항을 찾을 수 없습니다."}
    else:
        return {
            "source": "api",
            "law_name": standard_name,
            "article": f"제{article_no}조",
            "조문명": article.get("조문명"),
            "조문": article.get("조문"),
            "항": article.get("항")
        }
