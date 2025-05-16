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
    version="4.4.0-lawmatch"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEBUG_MODE = True

# 반드시 약칭 → 정식명칭 매핑
KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    # 아래와 같이 필요한 만큼 추가!
    "아동복지법": "아동복지법",
    "개인정보보호법": "개인정보 보호법",
}

@app.get("/")
def root():
    return {"message": "School LawBot API is running."}

@app.get("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/privacy-policy")
def privacy_policy():
    return {
        "message": "본 서비스의 개인정보 처리방침은 다음 링크에서 확인할 수 있습니다.",
        "url": "https://YOURDOMAIN.com/privacy-policy"
    }

def resolve_full_law_name(law_name: str) -> str:
    """
    약칭, 띄어쓰기, 오타 등 다양한 입력을 KNOWN_LAWS 딕셔너리 기반 정식명칭으로 변환
    """
    name = law_name.replace(" ", "").strip()
    for k, v in KNOWN_LAWS.items():
        if name == k.replace(" ", ""):
            return v
    return law_name  # 못 찾으면 원본 반환

def normalize_law_name(name: str) -> str:
    return name.replace(" ", "").strip()

def get_law_id(law_name: str, api_key: str) -> Optional[str]:
    normalized = normalize_law_name(law_name)
    try:
        print(f"▶ 사용 중인 OC_KEY: {api_key}")
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "query": law_name,
            "pIndex": 1,
            "pSize": 10
        })
        print("[DEBUG] lawSearch URL:", res.url)
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        if DEBUG_MODE:
            print(f"[DEBUG] lawSearch 응답 키 목록: {list(data.keys())}")
            print("[DEBUG] lawSearch 응답 일부:", str(res.text)[:300])
        law_root = data.get("Law") or data.get("법령") or {}
        laws = law_root.get("laws", {}).get("law") or law_root.get("law")
        if not laws:
            print("[DEBUG] ❌ law 리스트가 비어 있음")
            return None
        if isinstance(laws, dict):
            laws = [laws]
        for law in laws:
            name_fields = [law.get("법령명한글", ""), law.get("법령약칭명", ""), law.get("법령명", "")]
            for name in name_fields:
                if normalize_law_name(name) == normalized:
                    print(f"[DEBUG] ✅ 법령 매칭 성공: {name} → ID: {law.get('법령ID')}")
                    return law.get("법령ID")
        for law in laws:
            if law.get("현행연혁코드") == "현행":
                print(f"[DEBUG] ⚠️ 정확한 매칭 실패 → '현행' 기준 ID 사용: {law.get('법령ID')}")
                return law.get("법령ID")
        return None
    except Exception as e:
        print("[lawId 오류]", e)
        return None

def extract_article(xml_text, article_no, clause_no=None, subclause_no=None):
    circled_nums = {'①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10'}
    try:
        data = xmltodict.parse(xml_text)
        law_dict = data.get("법령", {})
        print("[DEBUG] law_dict keys:", list(law_dict.keys()))
        articles = law_dict.get("조문", {}).get("조문단위") if law_dict.get("조문") else None
        print("[DEBUG] articles (조문단위):", articles)
        if not articles:
            print("[DEBUG] articles가 None 또는 비어 있음")
            return "조문 정보가 존재하지 않습니다."
        if isinstance(articles, dict):
            articles = [articles]
        for article in articles:
            art_num = article.get("조문번호")
            print(f"[DEBUG] 현재 art_num: {art_num} / 요청 article_no: {article_no}")
            if art_num == str(article_no):
                # 항 파싱
                clauses = article.get("항")
                print("[DEBUG] clauses:", clauses)
                if not clause_no:
                    return article.get("조문내용", "내용 없음")
                if not clauses:
                    return "요청한 항을 찾을 수 없습니다."
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses:
                    clause_num = clause.get("항번호", "").strip()
                    clause_num_arabic = circled_nums.get(clause_num, clause_num)
                    print(f"[DEBUG] 현재 clause_num: {clause_num}({clause_num_arabic}) / 요청 clause_no: {clause_no}")
                    if clause_num_arabic == str(clause_no) or clause_num == str(clause_no):
                        # 호 파싱
                        if not subclause_no:
                            return clause.get("항내용", "내용 없음")
                        subclauses = clause.get("호")
                        print("[DEBUG] subclauses (호):", subclauses)
                        if not subclauses:
                            return "요청한 호를 찾을 수 없습니다."
                        if isinstance(subclauses, dict):
                            subclauses = [subclauses]
                        for sub in subclauses:
                            sub_num = sub.get("호번호", "").strip()
                            print(f"[DEBUG] 현재 sub_num: {sub_num} / 요청 subclause_no: {subclause_no}")
                            if sub_num == str(subclause_no):
                                return sub.get("호내용", "내용 없음")
                        return "요청한 호를 찾을 수 없습니다."
                return "요청한 항을 찾을 수 없습니다."
        return "요청한 조문을 찾을 수 없습니다."
    except Exception as e:
        print("[Parsing Error]", e)
        return "조문 정보가 존재하지 않습니다."

@app.get("/law", summary="법령 조문 조회")
def get_law_clause(
    law_name: str = Query(..., example="학교폭력예방법"),
    article_no: str = Query(..., example="16"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    api_key: str = Query(..., description="GPTs에서 전달되는 API 키")
):
    try:
        print(f"📥 요청: {law_name} 제{article_no}조 {clause_no or ''}항 {subclause_no or ''}호")
        law_name_full = resolve_full_law_name(law_name)
        print(f"[DEBUG] 정식 법령명 변환: {law_name} → {law_name_full}")
        law_id = get_law_id(law_name_full, api_key)
        print(f"[DEBUG] ➡ law_id: {law_id}")
        if not law_id:
            return JSONResponse(content={"error": "법령 ID 조회 실패"}, status_code=404)
        res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "ID": law_id,
            "pIndex": 1,
            "pSize": 1000
        })
        print("[DEBUG] lawService URL:", res.url)
        res.raise_for_status()
        if "법령이 없습니다" in res.text:
            print("[DEBUG] lawService 결과: 법령이 없습니다")
            return JSONResponse(content={"error": "해당 법령은 조회할 수 없습니다."}, status_code=403)
        내용 = extract_article(res.text, article_no, clause_no, subclause_no)
        return JSONResponse(content={
            "source": "api",
            "출처": "lawService",
            "법령명": law_name_full,
            "조문": f"{article_no}조",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "법령링크": f"https://www.law.go.kr/법령/{quote(law_name_full, safe='')}/{article_no}조"
        })
    except Exception as e:
        print("🚨 API 에러:", e)
        return JSONResponse(content={"error": "API 호출 실패"}, status_code=500)
