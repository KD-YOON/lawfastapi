from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
import os

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API 기반 실시간 조문·항·호 조회 서비스",
    version="3.6.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OC_KEY = os.getenv("OC_KEY")
DEBUG_MODE = True

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률"
}


@app.get("/")
def root():
    return {"message": "LawBot API is running"}


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


def resolve_full_law_name(law_name):
    return KNOWN_LAWS.get(law_name.strip(), law_name)


def normalize_law_name(name):
    return name.replace(" ", "").strip()


def get_law_id(law_name):
    normalized = normalize_law_name(law_name)
    try:
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "query": law_name
        })
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        laws = (
            data.get("LawSearch", {}).get("laws", {}).get("law") or
            data.get("LawSearch", {}).get("law")
        )
        if not laws:
            return None
        if isinstance(laws, dict):
            laws = [laws]

        for law in laws:
            for field in ["법령명한글", "법령약칭명", "법령명"]:
                if normalize_law_name(law.get(field, "")) == normalized:
                    return law.get("법령ID")

        for law in laws:
            if law.get("현행연혁코드") == "현행":
                return law.get("법령ID")

        return None
    except Exception as e:
        if DEBUG_MODE:
            print("[lawId 오류]", e)
        return None


def extract_article(xml_text, article_no, clause_no=None, subclause_no=None):
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("Law", {})
        articles = law.get("article")
        if isinstance(articles, dict):
            articles = [articles]

        for article in articles:
            if article.get("ArticleTitle") != f"제{article_no}조":
                continue

            clauses = article.get("Paragraph")

            if not clauses:
                return article.get("ArticleContent", "해당 조문에 항 정보가 없습니다.")

            if isinstance(clauses, dict):
                clauses = [clauses]

            for clause in clauses:
                if clause_no is None or clause.get("ParagraphNum") == clause_no:
                    subclauses = clause.get("SubParagraph")

                    if DEBUG_MODE:
                        print("🟡 SubParagraph 구조:", subclauses)

                    if subclause_no:
                        if not subclauses:
                            return "요청한 호가 존재하지 않습니다."
                        if isinstance(subclauses, dict):
                            subclauses = [subclauses]

                        for sub in subclauses:
                            # 일반 구조
                            num = sub.get("SubParagraphNum") or sub.get("@SubParagraphNum")
                            content = sub.get("SubParagraphContent") or sub.get("#text")

                            if num == subclause_no:
                                return content or "내용 없음"

                        return "요청한 호를 찾을 수 없습니다."

                    return clause.get("ParagraphContent", "내용 없음")

            return "요청한 항을 찾을 수 없습니다."

        return "요청한 조문을 찾을 수 없습니다."
    except Exception as e:
        if DEBUG_MODE:
            print("[Parsing Error]", e)
            print(xml_text[:500])
        return "조문 정보가 존재하지 않습니다."


@app.get("/law", summary="법령 조문 조회", description="법령명, 조문 번호, 항 번호, 호 번호를 기준으로 해당 법령 내용을 조회합니다.")
def get_law_clause(
    law_name: str = Query(..., example="학교폭력예방법"),
    article_no: str = Query(..., example="16"),
    clause_no: Optional[str] = Query(None, example="1"),
    subclause_no: Optional[str] = Query(None, example="2")
):
    try:
        print(f"📥 요청: {law_name} 제{article_no}조 {clause_no or ''}항 {subclause_no or ''}호")
        law_name = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name)
        print(f"➡️ law_id: {law_id}")
        if not law_id:
            return JSONResponse(content={"error": "법령 ID 조회 실패"}, status_code=404)

        res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
            "OC": OC_KEY,
            "target": "law",
            "type": "XML",
            "ID": law_id
        })
        res.raise_for_status()

        if "법령이 없습니다" in res.text:
            return JSONResponse(content={
                "error": "해당 법령은 OC 키로 조회할 수 없습니다.",
                "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/제{article_no}조"
            }, status_code=403)

        내용 = extract_article(res.text, article_no, clause_no, subclause_no)

        return JSONResponse(content={
            "source": "api",
            "출처": "lawService",
            "법령명": law_name,
            "조문": f"제{article_no}조",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name, safe='')}/제{article_no}조"
        })

    except Exception as e:
        if DEBUG_MODE:
            print("🚨 API 에러:", e)
        return JSONResponse(content={"error": "API 호출 실패"}, status_code=500)
