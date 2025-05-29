import os
import re
import datetime
import requests
import xmltodict
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
from time import time

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
    title="School LawBot API",
    description="국가법령정보센터 DRF API 기반 실시간 조문·가지조문·항·호 구조화 및 UX/가이드 메타데이터 자동화",
    version="10.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

KNOWN_LAWS = {
    "학교폭력예방법": "학교폭력예방 및 대책에 관한 법률",
    "학교폭력예방법 시행령": "학교폭력예방 및 대책에 관한 법률 시행령",
    "개인정보보호법": "개인정보 보호법",
}

recent_logs = []

def resolve_full_law_name(law_name: str) -> str:
    name = law_name.replace(" ", "").strip()
    for k, v in KNOWN_LAWS.items():
        if name == k.replace(" ", ""):
            return v
    return law_name

def normalize_law_name(name: str) -> str:
    return name.replace(" ", "").strip()

def normalize_article_no(article_no_raw):
    if not article_no_raw:
        return article_no_raw
    s = article_no_raw.replace(" ", "")
    s = re.sub(r"제(\d+)조조", r"제\1조", s)
    s = re.sub(r"(\d+)조조", r"\1조", s)
    return s

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

def parse_article_input(article_no_raw):
    if not article_no_raw:
        return None, None, None, None, False
    s = article_no_raw.replace(" ", "")
    m = re.match(r"제(\d+)조의(\d+)(?:제(\d+)항)?(?:제(\d+)호)?", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None, int(m.group(4)) if m.group(4) else None, True
    m = re.match(r"제(\d+)조(?:제(\d+)항)?(?:제(\d+)호)?", s)
    if m:
        return int(m.group(1)), None, int(m.group(2)) if m.group(2) else None, int(m.group(3)) if m.group(3) else None, False
    return None, None, None, None, False

def make_article_link(law_name, article_no):
    law_url_name = quote(law_name.replace(" ", ""), safe='')
    if article_no:
        article_path = quote(fix_article_no(article_no), safe='')
        return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"
    else:
        return f"https://www.law.go.kr/법령/{law_url_name}"

def split_article_text_to_structure(text):
    gaji_pattern = re.compile(r'(제\d+조의\d+)[\s:.\)]*')
    hang_pattern = re.compile(r'(제\d+항)[\s:.\)]*')
    ho_pattern = re.compile(r'(제\d+호)[\s:.\)]*')

    result = {}
    gaji_splits = gaji_pattern.split(text)
    if len(gaji_splits) > 1:
        for i in range(1, len(gaji_splits), 2):
            gaji_title = gaji_splits[i]
            gaji_content = gaji_splits[i+1] if i+1 < len(gaji_splits) else ""
            result[gaji_title] = split_article_text_to_structure(gaji_content)
        return result

    hang_splits = hang_pattern.split(text)
    if len(hang_splits) > 1:
        hang_dict = {}
        preface = hang_splits[0]
        for i in range(1, len(hang_splits), 2):
            hang_title = hang_splits[i]
            hang_content = hang_splits[i+1] if i+1 < len(hang_splits) else ""
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
        result = {'머릿말': preface.strip(), '항': hang_dict}
        return result

    return text.strip()

def get_law_id(law_name: str, api_key: str) -> Optional[str]:
    normalized = normalize_law_name(law_name)
    try:
        res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "query": law_name,
            "pIndex": 1,
            "pSize": 10
        })
        res.raise_for_status()
        data = xmltodict.parse(res.text)
        law_root = data.get("LawSearch") or data.get("lawSearch") or {}
        laws = law_root.get("laws", {}).get("law") or law_root.get("law")
        if not laws:
            return None
        if isinstance(laws, dict):
            laws = [laws]
        for law in laws:
            name_fields = [law.get("법령명한글", ""), law.get("법령약칭명", ""), law.get("법령명", "")]
            for name in name_fields:
                if normalize_law_name(name) == normalized:
                    return law.get("법령ID")
        for law in laws:
            if law.get("현행연혁코드") == "현행":
                return law.get("법령ID")
        return None
    except Exception as e:
        print("[lawId 오류]", e)
        return None

def extract_article_with_full(xml_text, article_no_raw, clause_no=None, subclause_no=None, law_name_full=None):
    circled_nums = {'①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10'}
    no, gaji, hang, ho, is_branch = parse_article_input(article_no_raw)
    canonical_article_no = None
    try:
        data = xmltodict.parse(xml_text)
        law = data.get("법령", {})
        all_articles = []
        paths = [
            ["조문", "조문단위"],
            ["조문", "조문조단위"],
            ["조문", "가지조문단위"],
            ["조문", "가지조문조단위"],
            ["조문", "별표단위"],
            ["조문", "부칙단위"]
        ]
        for path in paths:
            cur = law
            try:
                for key in path:
                    cur = cur.get(key, {})
                if isinstance(cur, dict):
                    cur = [cur]
                if cur:
                    all_articles.extend(cur)
            except Exception:
                continue
        available = []
        matched_article = None
        for idx, article in enumerate(all_articles):
            no_raw = str(article.get("조문번호", "0"))
            this_article_name = no_raw
            is_gaji = "의" in no_raw
            available.append(this_article_name)
            if normalize_article_no(this_article_name) == normalize_article_no(article_no_raw):
                matched_article = article
                canonical_article_no = this_article_name
                full_article = article.get("조문내용", "내용 없음")
                if is_gaji:
                    if full_article and full_article != "내용 없음":
                        return full_article, full_article, available, canonical_article_no, split_article_text_to_structure(full_article)
                    else:
                        안내 = (
                            f"해당 가지조문(조문번호: {this_article_name})은 시스템에서 자동 추출이 불가합니다.<br>"
                            f"아래 국가법령정보센터 바로가기를 확인해 주세요.<br>"
                            f"<a href='{make_article_link(law_name_full, article_no_raw)}'>국가법령정보센터 바로가기</a>"
                        )
                        return 안내, "", available, canonical_article_no, None
                if hang is None:
                    return full_article, full_article, available, canonical_article_no, split_article_text_to_structure(full_article)
                clauses = article.get("항", [])
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses:
                    cnum = clause.get("항번호", "").strip()
                    cnum_arabic = circled_nums.get(cnum, cnum)
                    if cnum_arabic == str(hang) or cnum == str(hang):
                        clause_content = clause.get("항내용", "내용 없음")
                        subclauses = clause.get("호", [])
                        if ho:
                            if isinstance(subclauses, dict):
                                subclauses = [subclauses]
                            for subclause in subclauses:
                                snum = subclause.get("호번호", "").strip()
                                if snum == str(ho):
                                    return subclause.get("호내용", "내용 없음"), full_article, available, canonical_article_no, None
                            return "요청한 호를 찾을 수 없습니다.", full_article, available, canonical_article_no, None
                        return clause_content, full_article, available, canonical_article_no, None
                return "요청한 항을 찾을 수 없습니다.", full_article, available, canonical_article_no, None
        return "", "", available, None, None
    except Exception as e:
        return f"파싱 오류: {e}", "", [], None, None

@app.get("/law", summary="법령 조문 조회")
@app.head("/law")
def get_law_clause(
    law_name: str = Query(None, example="학교폭력예방법시행령"),
    article_no: str = Query(None, example="14의2"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    device: Optional[str] = Query(None),  # 모바일/PC 구분용
    request: Request = None
):
    start_time = time()
    if not law_name or not article_no:
        return add_privacy_notice({
            "found": False,
            "error": "law_name, article_no 파라미터는 필수입니다.",
            "guide": "예시: ‘14’, ‘14의2’, ‘제14조의2’ 모두 입력 가능. 띄어쓰기는 무시됩니다."
        })
    api_key = API_KEY
    law_name_full = resolve_full_law_name(law_name)
    article_no_user = article_no
    article_no_corrected = fix_article_no(article_no)
    law_id = get_law_id(law_name_full, api_key)
    service_status = "OK" if law_id else "NOT_FOUND"
    processing_time = "%.2fs" % (time() - start_time)
    last_updated = datetime.datetime.now().isoformat()

    if not law_id:
        return add_privacy_notice({
            "found": False,
            "message": "법령명을 찾을 수 없습니다.",
            "lawName": law_name_full,
            "userInput": article_no_user,
            "correctedArticleNo": article_no_corrected,
            "guide": "‘14’, ‘14의2’, ‘제14조의2’ 모두 입력 가능. 띄어쓰기는 무시됩니다.",
            "directLink": make_article_link(law_name_full, None),
            "serviceStatus": service_status,
            "processingTime": processing_time,
            "lastUpdated": last_updated,
            "viewType": "responsive-card" if (device == "mobile") else "table"
        })

    res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
        "OC": api_key,
        "target": "law",
        "type": "XML",
        "ID": law_id,
        "pIndex": 1,
        "pSize": 1000
    })
    res.raise_for_status()
    article_no_norm = normalize_article_no(article_no)
    내용, 조문전체, available_articles, canonical_article_no, 구조화 = extract_article_with_full(
        res.text, article_no_norm, clause_no, subclause_no, law_name_full
    )

    # 요약 생성(본문 앞 100자)
    summary = ""
    if 조문전체:
        summary = 조문전체[:100].replace("\n", " ") + ("..." if len(조문전체) > 100 else "")
    elif 내용 and 내용 != "":
        summary = 내용[:100].replace("\n", " ") + ("..." if len(내용) > 100 else "")

    found = bool(조문전체 and "없습니다" not in 조문전체 and len(조문전체.strip()) > 10)
    message = (
        "정확히 일치하는 조문이 없습니다. 아래 목록에서 선택하거나 다시 입력해 주세요."
        if not found else "정상적으로 조회되었습니다."
    )

    result = {
        "lawName": law_name_full,
        "articleNo": canonical_article_no or article_no_corrected,
        "userInput": article_no_user,
        "correctedArticleNo": article_no_corrected,
        "found": found,
        "message": message,
        "articleContent": 조문전체 if found else "",
        "summary": summary,
        "structure": 구조화,
        "articleList": available_articles,
        "related": available_articles[:5] if available_articles else [],
        "guide": "‘14’, ‘14의2’, ‘제14조의2’ 등 자유롭게 입력 가능. 띄어쓰기는 무시됩니다.",
        "directLink": make_article_link(law_name_full, canonical_article_no or article_no_corrected),
        "viewType": "responsive-card" if (device == "mobile") else "table",
        "shortcutButton": True,
        "serviceStatus": service_status,
        "processingTime": processing_time,
        "lastUpdated": last_updated,
        "usageExample": "피해학생 보호조치 결정의 기준으로 활용됩니다."  # 실제 활용사례 DB 연동 가능
    }
    return add_privacy_notice(result)

@app.get("/")
@app.head("/")
def root():
    return add_privacy_notice({"message": "School LawBot API is running."})

@app.get("/healthz")
@app.head("/healthz")
def health_check():
    return add_privacy_notice({"status": "ok"})

@app.get("/ping")
@app.head("/ping")
def ping():
    return add_privacy_notice({"status": "ok"})

@app.get("/privacy-policy")
def privacy_policy():
    return add_privacy_notice({
        "message": "본 서비스의 개인정보 처리방침은 다음 링크에서 확인할 수 있습니다.",
        "url": PRIVACY_URL
    })

@app.get("/test-log", summary="최근 요청 로그 10건 조회")
@app.head("/test-log")
def test_log():
    return add_privacy_notice({"recent_logs": recent_logs[-10:]})
