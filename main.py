import os
import re
import requests
import xmltodict
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
from bs4 import BeautifulSoup
import datetime

PRIVACY_URL = "https://github.com/KD-YOON/privacy-policy"
PRIVACY_NOTICE = (
    "본 서비스의 개인정보 처리방침은 https://github.com/KD-YOON/privacy-policy 에서 확인할 수 있습니다. "
    "※ 동의/허용 안내 반복 방지는 반드시 프론트(웹/앱/챗봇)에서 동의 이력 저장 및 제어해야 합니다."
)

def add_privacy_notice(data):
    if isinstance(data, dict):
        data['privacy_notice'] = PRIVACY_NOTICE
        data['privacy_policy_url'] = PRIVACY_URL
    return data

API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="LawBot (API+크롤링 완전 혼합 자동화)",
    description="DRF API+HTML(iframe) 크롤링 혼합 구조, 가지조문/항/호 중첩 자동 구조화, 마크다운/링크/로깅/프라이버시 안내 등 실전형 완성본",
    version="2.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

recent_logs = []

def split_article_text_to_structure(text):
    # 가지조문→항→호 순서로 재귀 구조 분리
    gaji_pattern = re.compile(r'(제\d+조의\d+)[\s:.\)]*')
    gaji_splits = gaji_pattern.split(text)
    if len(gaji_splits) > 1:
        result = {}
        for i in range(1, len(gaji_splits), 2):
            gaji_title = gaji_splits[i]
            gaji_content = gaji_splits[i+1] if i+1 < len(gaji_splits) else ""
            result[gaji_title] = split_article_text_to_structure(gaji_content)
        return result
    hang_pattern = re.compile(r'(제\d+항)[\s:.\)]*')
    hang_splits = hang_pattern.split(text)
    if len(hang_splits) > 1:
        hang_dict = {}
        preface = hang_splits[0]
        for i in range(1, len(hang_splits), 2):
            hang_title = hang_splits[i]
            hang_content = hang_splits[i+1] if i+1 < len(hang_splits) else ""
            ho_pattern = re.compile(r'(제\d+호)[\s:.\)]*')
            ho_splits = ho_pattern.split(hang_content)
            if len(ho_splits) > 1:
                ho_dict = {}
                ho_preface = ho_splits[0]
                for j in range(1, len(ho_splits), 2):
                    ho_title = ho_splits[j]
                    ho_content = ho_splits[j+1] if j+1 < len(ho_splits) else ""
                    ho_dict[ho_title] = ho_content.strip()
                hang_dict[hang_title] = {'본문': ho_preface.strip(), '호': ho_dict}
            else:
                hang_dict[hang_title] = hang_content.strip()
        return {'머릿말': preface.strip(), '항': hang_dict}
    return text.strip()

def make_article_link(law_name, article_no):
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    article_path = quote(article_no.replace(" ", ""), safe='')
    return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"

def get_law_id(law_name: str, api_key: str) -> Optional[str]:
    normalized = law_name.replace(" ", "")
    try:
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": api_key, "target": "law", "type": "XML",
            "query": law_name, "pIndex": 1, "pSize": 10
        })
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        laws = data.get("LawSearch", {}).get("laws", {}).get("law")
        if not laws:
            return None
        if isinstance(laws, dict):
            laws = [laws]
        for law in laws:
            names = [law.get("법령명한글", ""), law.get("법령약칭명", ""), law.get("법령명", "")]
            for name in names:
                if name.replace(" ", "") == normalized:
                    return law.get("법령ID")
        for law in laws:
            if law.get("현행연혁코드") == "현행":
                return law.get("법령ID")
        return None
    except Exception as e:
        print("[lawId 오류]", e)
        return None

def extract_article_from_api(xml_text, article_no):
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("법령", {})
        articles = []
        for k in ["조문단위", "조문조단위", "가지조문단위", "가지조문조단위"]:
            sub = law.get("조문", {}).get(k)
            if sub:
                if isinstance(sub, dict):
                    articles.append(sub)
                else:
                    articles.extend(sub)
        available = []
        for article in articles:
            no = str(article.get("조문번호", ""))
            available.append(no)
            if no.replace(" ", "") == article_no.replace(" ", ""):
                content = article.get("조문내용", "내용 없음")
                return content, available
        return None, available
    except Exception as e:
        print("API 파싱 오류:", e)
        return None, []

def extract_iframe_src(html):
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe", id="lawService")
    if not iframe:
        return None
    src = iframe.get("src")
    if src and src.startswith("/"):
        src = "https://www.law.go.kr" + src
    return src

def fetch_jomun_html(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        return None, f"[1차 요청 실패] {e}"
    src = extract_iframe_src(r.text)
    if not src:
        return None, "iframe src를 찾지 못했습니다."
    try:
        r2 = requests.get(src, timeout=10)
        r2.raise_for_status()
    except Exception as e:
        return None, f"[2차(iframe) 요청 실패] {e}"
    return r2.text, None

def parse_jomun_from_html(html, article_no):
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        lambda s: s.find_all(lambda tag: tag.name in ['h3', 'strong'] and article_no in tag.get_text()),
        lambda s: s.find_all(lambda tag: tag.name == 'div' and article_no in tag.get_text()),
        lambda s: s.find_all(lambda tag: tag.name == 'li' and article_no in tag.get_text())
    ]
    for selector in selectors:
        tags = selector(soup)
        for tag in tags:
            parent = tag.find_parent(['div','li','section']) or tag
            text = parent.get_text(separator="\n", strip=True)
            if article_no in text and len(text) > 20:
                return text
    all_text = soup.get_text(separator="\n", strip=True)
    structure = split_article_text_to_structure(all_text)
    if article_no in structure:
        return structure[article_no]
    return None

def make_markdown_table(law_name, article_no, 내용, 법령링크, 구조화, available_articles=None):
    내용_fmt = 내용.replace("|", "\\|").replace("\n", "<br>")
    tbl = (
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 법령명 | {law_name} |\n"
        f"| 조문 | {article_no or ''} |\n"
        f"| 내용 | {내용_fmt} |\n"
        f"| 구조화 | {str(구조화) if 구조화 else ''} |\n"
        f"| 출처 | [법령정보센터 바로가기]({법령링크}) |\n"
    )
    if available_articles:
        tbl += f"| 조회가능 조문번호 | {', '.join(available_articles)} |\n"
    return tbl

@app.get("/law-ultimate", summary="법령명+조문번호 → DRF API+HTML 혼합+구조화+마크다운+로깅")
def law_ultimate(
    law_name: str = Query(..., description="법령명 (예: 학교폭력예방및대책에관한법률시행령)"),
    article_no: str = Query(..., description="조문번호 (예: 제14조의3)"),
    request: Request = None
):
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "client_ip": request.client.host if request else "unknown",
        "law_name": law_name,
        "article_no": article_no,
    }
    try:
        # 1. API 우선
        law_id = get_law_id(law_name, API_KEY)
        api_available = []
        if law_id:
            res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
                "OC": API_KEY, "target": "law", "type": "XML", "ID": law_id, "pIndex": 1, "pSize": 1000
            })
            res.raise_for_status()
            if "법령이 없습니다" not in res.text:
                api_content, api_available = extract_article_from_api(res.text, article_no)
                if api_content and len(api_content.strip()) > 5 and "없음" not in api_content:
                    구조화 = split_article_text_to_structure(api_content)
                    law_url = make_article_link(law_name, article_no)
                    markdown = make_markdown_table(
                        law_name, article_no, api_content, law_url, 구조화, api_available
                    )
                    result = {
                        "source": "API",
                        "법령명": law_name,
                        "조문번호": article_no,
                        "내용": api_content,
                        "구조화": 구조화,
                        "법령센터바로가기": f"<a href='{law_url}'>법령정보센터 바로가기</a>",
                        "markdown": markdown,
                        "조문목록": api_available
                    }
                    log_entry["status"] = "success(API)"
                    log_entry["result"] = result
                    recent_logs.append(log_entry)
                    if len(recent_logs) > 50:
                        recent_logs.pop(0)
                    return JSONResponse(content=add_privacy_notice(result))
        # 2. fallback: 크롤링
        url = make_article_link(law_name, article_no)
        html2, error = fetch_jomun_html(url)
        if error:
            log_entry["status"] = "error"
            log_entry["error"] = error
            recent_logs.append(log_entry)
            if len(recent_logs) > 50:
                recent_logs.pop(0)
            return JSONResponse(content=add_privacy_notice({"error": error, "url": url}))
        jomun_text = parse_jomun_from_html(html2, article_no)
        if not jomun_text:
            structure = split_article_text_to_structure(html2)
            result_content = structure.get(article_no, "조문을 찾지 못했습니다.")
        else:
            structure = split_article_text_to_structure(jomun_text)
            result_content = structure if isinstance(structure, dict) else {"본문": structure}
        law_url = make_article_link(law_name, article_no)
        markdown = make_markdown_table(
            law_name, article_no, str(result_content), law_url, structure
        )
        result = {
            "source": "HTML크롤링",
            "법령명": law_name,
            "조문번호": article_no,
            "내용": str(result_content),
            "구조화": structure,
            "법령센터바로가기": f"<a href='{law_url}'>법령정보센터 바로가기</a>",
            "markdown": markdown,
            "조문목록": []
        }
        log_entry["status"] = "success(HTML)"
        log_entry["result"] = result
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        return JSONResponse(content=add_privacy_notice(result))
    except Exception as e:
        log_entry["status"] = "error"
        log_entry["error"] = str(e)
        recent_logs.append(log_entry)
        if len(recent_logs) > 50:
            recent_logs.pop(0)
        return JSONResponse(content=add_privacy_notice({"error": "API/크롤링 실패", "detail": str(e)}), status_code=500)

@app.get("/law-ultimate-logs")
def law_ultimate_logs():
    return add_privacy_notice({"recent_logs": recent_logs[-10:]})

@app.get("/")
def root():
    return add_privacy_notice({"message": "LawBot (API+크롤링 완전체) is running."})
