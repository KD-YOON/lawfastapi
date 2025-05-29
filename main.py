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
from bs4 import BeautifulSoup

# ---------- 기본 안내 및 개인정보 처리방침 ----------
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

# ---------- 환경 변수 및 FastAPI ----------
API_KEY = os.environ.get("OC_KEY", "default_key")

app = FastAPI(
    title="School LawBot API",
    description="국가법령정보센터 API + 백업 JSON + 크롤링 자동화 기반 실시간 조문·가지조문·항·호 구조화",
    version="10.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 약식 법령명 자동 변환 ----------
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

# ---------- 조문 번호 및 입력값 표준화 ----------
def fix_article_no(article_no):
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

# ---------- 조문/가지조문/항/호 구조 분리 ----------
def split_article_text_to_structure(text):
    # 정규식 기반 가지조문, 항, 호 분리
    gaji_pattern = re.compile(r'(제\d+조의\d+)[\s:.\)]*')
    hang_pattern = re.compile(r'(제\d+항)[\s:.\)]*')
    ho_pattern = re.compile(r'(제\d+호)[\s:.\)]*')
    result = {}

    # 가지조문 분리
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

    # 기본(더 이상 분할 불가)
    return text.strip()

# ---------- 실시간 API + 백업 JSON 연동 ----------
def call_law_api(law_name, article_no):
    # (실제 API 연동 예시)
    base_url = "https://www.law.go.kr/DRF/lawService.do"
    params = {
        "OC": API_KEY,
        "target": "lawtext",
        "ID": "",
        "type": "XML",
        "name": law_name
    }
    # 실제 API 활용시 ID/조문번호 등 추가 구현 필요
    try:
        res = requests.get(base_url, params=params, timeout=5)
        res.raise_for_status()
        data = xmltodict.parse(res.content)
        # (실제 조문 파싱 로직 필요)
        return data, None
    except Exception as e:
        return None, str(e)

def load_backup_json():
    try:
        with open("backup.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_article_from_backup(law_name, article_no):
    data = load_backup_json()
    law_dict = data.get(law_name, {})
    return law_dict.get(article_no, "")

# ---------- 본문-링크 동기화 및 자동 오류/누락 안내 ----------
def make_article_link(law_name, article_no):
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    article_path = quote(fix_article_no(article_no), safe='')
    return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"

def auto_notice(text, context):
    if not text or text.strip() == '':
        return f'[자동안내] {context}가 누락 또는 오답입니다. 원문/링크 확인 필요'
    return ''

def structure_article(law_name, article_no, text):
    """가지조문/본문/링크/오류 자동 동기화 & 표준 JSON 변환"""
    struct = split_article_text_to_structure(text)
    result = {}
    # 최상위가 가지조문인지, 본문인지 구분
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

# ---------- 핵심 API: 실시간+백업+자동 오류안내+표준응답 ----------
@app.get("/law/article")
def get_law_article(
    law_name: str = Query(..., description="법령명"),
    article_no: str = Query(..., description="조문 번호(예: 14, 14의3, 제14조, 제14조의3 등)")
):
    """
    [실무] 실시간 API + 백업 JSON 자동 fallback + 가지조문/본문/링크/오류 표준 구조
    """
    full_law_name = resolve_full_law_name(law_name)
    article_no_fixed = fix_article_no(article_no)

    # 1. 실시간 API 시도
    data, api_error = call_law_api(full_law_name, article_no_fixed)
    text = None
    source = "api"

    # 실제 본문 파싱(실제 상황에 맞게 보정 필요, 여기선 샘플)
    if data and '법령본문' in data:
        text = data['법령본문']
    elif api_error:
        # 2. API 실패 시 백업 JSON에서 조회
        text = get_article_from_backup(full_law_name, article_no_fixed)
        source = "backup"

    # 결과 없는 경우 자동 안내
    if not text:
        return JSONResponse(
            content={
                "status": "fail",
                "source": source,
                "law_name": law_name,
                "article_no": article_no,
                "error": api_error or "해당 조문이 없습니다. (API+백업 모두 실패)"
            },
            status_code=404
        )

    # 3. 자동 구조화 및 안내문 생성
    article_struct = structure_article(full_law_name, article_no_fixed, text)

    # 4. 표준 JSON 응답
    response = {
        "status": "ok",
        "source": source,
        "law_name": full_law_name,
        "article_no": article_no_fixed,
        "article": article_struct,
        "notice": "가지조문/본문/링크/구조 자동 동기화 및 오류/누락 자동 안내"
    }
    return add_privacy_notice(response)

# ---------- 기본 루트 ----------
@app.get("/")
def root():
    return {"msg": "School LawBot API (최적화/자동화)", "date": str(datetime.datetime.now())}

