import os
import re
import json
import datetime
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict

# ----------------- [기본 안내 및 개인정보 처리방침] -----------------
PRIVACY_URL = "https://github.com/KD-YOON/privacy-policy"
PRIVACY_NOTICE = (
    "본 서비스의 개인정보 처리방침은 https://github.com/KD-YOON/privacy-policy 에서 확인할 수 있습니다. "
    "※ 동의/허용 안내 반복 방지는 반드시 프론트(웹/앱/챗봇)에서 동의 이력 저장 및 제어해야 합니다."
)

def add_privacy_notice(data):
    """JSON 응답에 개인정보 안내 자동 삽입"""
    if isinstance(data, dict):
        data['privacy_notice'] = PRIVACY_NOTICE
        data['privacy_policy_url'] = PRIVACY_URL
    return data

# ----------------- [환경 변수 및 FastAPI 기본설정] -----------------
API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 DRF API + JSON 백업 + 가지조문/항/호 구조화 자동화",
    version="10.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- [법령명 정규화 및 약식명 보정] -----------------
KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "개인정보보호법": "개인정보 보호법",
}

def resolve_full_law_name(law_name: str) -> str:
    name = law_name.replace(" ", "").strip()
    for k, v in KNOWN_LAWS.items():
        if name == k.replace(" ", ""):
            return v
    return law_name

# ----------------- [조문 번호 표준화 및 변환] -----------------
def fix_article_no(article_no):
    """
    '14' → '제14조', '17의3' → '제17조의3', 이미 포맷이면 그대로
    """
    s = str(article_no).replace(" ", "")
    if re.match(r'^제\d+조(의\d+)?$', s):
        return s
    if s.isdigit():
        return f'제{s}조'
    m = re.match(r"^(\d+)의(\d+)$", s)
    if m:
        return f"제{m.group(1)}조의{m.group(2)}"
    if not s.startswith('제'):
        s = '제' + s
    if not ('조' in s):
        s = s + '조'
    return s

# ----------------- [조문/가지조문/항/호 구조 분리] -----------------
def split_article_text_to_structure(text):
    """가지조문, 항, 호를 dict 구조로 분할"""
    gaji_pattern = re.compile(r'(제\d+조의\d+)[\s:.\)]*')
    hang_pattern = re.compile(r'(제\d+항)[\s:.\)]*')
    ho_pattern = re.compile(r'(제\d+호)[\s:.\)]*')

    result = {}

    # 가지조문 분리 (제N조의M)
    gaji_splits = gaji_pattern.split(text)
    if len(gaji_splits) > 1:
        for i in range(1, len(gaji_splits), 2):
            gaji_title = gaji_splits[i]
            gaji_content = gaji_splits[i+1] if i+1 < len(gaji_splits) else ""
            result[gaji_title] = split_article_text_to_structure(gaji_content)
        return result

    # 항 분리
    hang_splits = hang_pattern.split(text)
    if len(hang_splits) > 1:
        hang_dict = {}
        preface = hang_splits[0].strip()
        for i in range(1, len(hang_splits), 2):
            hang_title = hang_splits[i]
            hang_content = hang_splits[i+1] if i+1 < len(hang_splits) else ""
            hang_dict[hang_title] = split_article_text_to_structure(hang_content)
        if preface:
            hang_dict['preface'] = preface
        return hang_dict

    # 호 분리
    ho_splits = ho_pattern.split(text)
    if len(ho_splits) > 1:
        ho_dict = {}
        preface = ho_splits[0].strip()
        for i in range(1, len(ho_splits), 2):
            ho_title = ho_splits[i]
            ho_content = ho_splits[i+1] if i+1 < len(ho_splits) else ""
            ho_dict[ho_title] = ho_content.strip()
        if preface:
            ho_dict['preface'] = preface
        return ho_dict

    # 더 이상 분할 불가(기본)
    return text.strip()

# ----------------- [실시간 API + 백업 JSON 자동화] -----------------
def get_law_data_from_api(law_name, article_no):
    """
    실제 API 호출(실전환경 맞춤 필요)
    """
    try:
        url = "https://www.law.go.kr/DRF/lawService.do"
        params = {
            "OC": API_KEY,
            "target": "lawtext",
            "type": "XML",
            "name": law_name,
        }
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = xmltodict.parse(response.content)
        # 아래 구조는 실제 법령마다 맞게 파싱 필요 (샘플)
        law_text = data.get('Law', {}).get('Article', {}).get('Content', "")
        return law_text
    except Exception:
        return None

def get_law_data_from_backup(law_name, article_no):
    """로컬 backup.json에서 법령 조문 반환"""
    try:
        with open("backup.json", encoding="utf-8") as f:
            backup = json.load(f)
        return backup.get(law_name, {}).get(article_no, "")
    except Exception:
        return ""

# ----------------- [링크 생성 및 자동 안내] -----------------
def make_article_link(law_name, article_no):
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    article_path = quote(fix_article_no(article_no), safe='')
    return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"

def auto_notice(text, context):
    """누락·오답 안내문 자동 생성"""
    if not text or text.strip() == '':
        return f'[자동안내] {context}가 누락 또는 오답입니다. 원문/링크 확인 필요'
    return ''

# ----------------- [가지조문/본문/링크 표준 구조화] -----------------
def structure_article(law_name, article_no, text):
    """가지조문/본문/링크/오류 자동 동기화 & 표준 JSON 변환"""
    struct = split_article_text_to_structure(text)
    result = {}
    if isinstance(struct, dict):
        for k, v in struct.items():
            if '의' in k:
                result[k] = {
                    "type": "gaji",
                    "link": make_article_link(law_name, k),
                    "notice": "가지조문은 링크만 제공, 본문은 생략"
                }
            else:
                result[k] = {
                    "type": "main",
                    "link": make_article_link(law_name, k),
                    "text": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False),
                    "notice": auto_notice(v, k)
                }
    elif isinstance(struct, str):
        result[fix_article_no(article_no)] = {
            "type": "main",
            "link": make_article_link(law_name, article_no),
            "text": struct,
            "notice": auto_notice(struct, fix_article_no(article_no))
        }
    return result

# ----------------- [API: 실시간+백업+자동 안내 표준 응답] -----------------
@app.get("/law/article")
def get_law_article(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="조문 번호(예: 14, 14의3, 제14조, 제14조의3 등)")
):
    """
    실시간 API + 백업 JSON 자동 fallback + 가지조문/본문/링크/오류 표준 구조
    """
    full_law_name = resolve_full_law_name(law_name)
    article_no_fixed = fix_article_no(article_no)

    # 1. API 시도
    text = get_law_data_from_api(full_law_name, article_no_fixed)
    source = "api"

    # 2. 실패시 백업 JSON
    if not text:
        text = get_law_data_from_backup(full_law_name, article_no_fixed)
        source = "backup"

    if not text:
        return JSONResponse(
            content={
                "status": "fail",
                "source": source,
                "law_name": law_name,
                "article_no": article_no,
                "error": "해당 조문이 없습니다. (API+백업 모두 실패)"
            },
            status_code=404
        )

    article_struct = structure_article(full_law_name, article_no_fixed, text)

    response = {
        "status": "ok",
        "source": source,
        "law_name": full_law_name,
        "article_no": article_no_fixed,
        "article": article_struct,
        "notice": "가지조문/본문/링크/구조 자동 동기화 및 오류/누락 자동 안내"
    }
    return add_privacy_notice(response)

# ----------------- [헬스체크 기본 루트] -----------------
@app.get("/")
def root():
    return {"msg": "School LawBot API (최적화/자동화)", "date": str(datetime.datetime.now())}
