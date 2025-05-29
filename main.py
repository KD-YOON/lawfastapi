import os
import re
import datetime
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from urllib.parse import quote
import requests
import xmltodict
from bs4 import BeautifulSoup

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
    description="국가법령정보센터 DRF API + HTML 크롤링 기반 실시간 조문·가지조문·항·호 구조화 자동화",
    version="9.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
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
    # 입력값에서 공백 등 제거만 (링크 생성은 fix_article_no에서 처리)
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
    # 완전체는 그대로 ('제14조', '제17조의3' 등)
    if re.match(r'^제\d+조(의\d+)?$', s):
        return s
    # '14' → '제14조'
    if s.isdigit():
        return f'제{s}조'
    # '17의3' → '제17조의3'
    m = re.match(r"^(\d+)의(\d+)$", s)
    if m:
        return f"제{m.group(1)}조의{m.group(2)}"
    # 혹시 앞뒤로 '제'/'조' 없는 이상한 값이면 마지막으로 보정
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
        article_path = quote(fix_article_no(article_no), safe='')  # fix_article_no를 거친다
        return f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"
    else:
        return f"https://www.law.go.kr/법령/{law_url_name}"

def split_article_text_to_structure(text):
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
        preface = hang_splits[0]
        for i in range(1, len(hang_splits), 2):
            hang_title = hang_splits[i]
            hang_content = hang_splits[i+1] if i+1 < len(hang_splits) else ""
            # 호 분리
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

def fetch_article_html_fallback(law_name_full, article_no):
    try:
        law_url_name = quote(law_name_full.replace(' ', ''), safe='')
        article_path = quote(fix_article_no(article_no), safe='')
        article_url = f"https://www.law.go.kr/법령/{law_url_name}/{article_path}"
        res = requests.get(article_url, timeout=7)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        selectors = [
            ".law-article .article", ".article", ".law-article", "#article", ".cont_article",
            ".contlawview", "#conContents",
        ]
        main = None
        for sel in selectors:
            main = soup.select_one(sel)
            if main:
                break
        if main:
            text = main.get_text(separator="\n", strip=True)
            if "조문 본문을 찾을 수 없습니다" not in text and len(text.strip()) > 20:
                return text, split_article_text_to_structure(text)

        text_blocks = []
        for tag in soup.find_all(['div', 'p', 'li', 'span', 'section']):
            t = tag.get_text(separator="\n", strip=True)
            if (
                len(t) > 20 and 
                re.search(r"(제\s*\d+조|항|호|가지조문|법령|목적|시행|벌칙)", t)
            ):
                text_blocks.append(t)
        all_text = "\n".join(text_blocks)
        if all_text and len(all_text) > 20:
            return all_text, split_article_text_to_structure(all_text)

        return "HTML에서 조문 본문을 찾을 수 없습니다.", None
    except Exception as e:
        return f"(HTML fallback 오류: {e})", None

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
        # Fallback: 본문 추출 실패, HTML에서 구조분리
        if law_name_full and article_no_raw:
            html_text, structured_json = fetch_article_html_fallback(law_name_full, article_no_raw)
            안내 = (
                f"API/DB에 조문 본문이 없어 웹페이지에서 추출했습니다.<br>"
                f"아래 국가법령정보센터 바로가기도 참고하세요.<br>"
                f"<a href='{make_article_link(law_name_full, article_no_raw)}'>국가법령정보센터 바로가기</a><br>"
                f"<br>본문:<br>{html_text if html_text else '웹페이지에서도 본문 추출 실패'}"
            )
            return (
                안내,
                html_text if html_text else "",
                available,
                canonical_article_no,
                structured_json
            )
        안내 = (
            f"요청한 조문({article_no_raw})을 찾을 수 없습니다.<br>"
            f"실제 조회 가능한 조문번호: {', '.join(available) if available else '없음'}<br>"
            f"아래 국가법령정보센터에서 직접 확인하세요.<br>"
            f"<a href='{make_article_link(law_name_full, None)}'>법령 바로가기</a>"
        )
        return 안내, "", available, None, None
    except Exception as e:
        return f"파싱 오류: {e}", "", [], None, None

def make_markdown_table(law_name, article_no, clause_no, subclause_no, 내용, 법령링크, 조문전체, available_articles=None):
    내용_fmt = 내용.replace("|", "\\|").replace("\n", "<br>")
    조문전체_fmt = 조문전체.replace("|", "\\|").replace("\n", "<br>")
    tbl = (
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 법령명 | {law_name} |\n"
        f"| 조문 | {article_no or ''} |\n"
        f"| 항 | {str(clause_no)+'항' if clause_no else ''} |\n"
        f"| 호 | {str(subclause_no)+'호' if subclause_no else ''} |\n"
        f"| 내용 | {내용_fmt} |\n"
        f"| 조문 전체 | {조문전체_fmt} |\n"
        f"| 출처 | [국가법령정보센터 바로가기]({법령링크}) |\n"
    )
    if available_articles:
        tbl += f"| 조회가능 조문번호 | {', '.join(available_articles)} |\n"
    return tbl

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

@app.get("/law", summary="법령 조문 조회")
@app.head("/law")
def get_law_clause(
    law_name: str = Query(None, example="학교폭력예방법시행령"),
    article_no: str = Query(None, example="제14조의 2"),
    clause_no: Optional[str] = Query(None),
    subclause_no: Optional[str] = Query(None),
    request: Request = None
):
    if not law_name or not article_no:
        return add_privacy_notice({
            "error": "law_name, article_no 파라미터는 필수입니다. 예시: /law?law_name=학교폭력예방법시행령&article_no=제14조의 2"
        })
    api_key = API_KEY
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "client_ip": request.client.host if request else "unknown",
        "law_name": law_name,
        "article_no": article_no,
        "clause_no": clause_no,
        "subclause_no": subclause_no,
        "api_key": api_key
    }
    try:
        law_name_full = resolve_full_law_name(law_name)
        law_id = get_law_id(law_name_full, api_key)
        if not law_id:
            log_entry["status"] = "error"
            log_entry["error"] = "법령 ID 조회 실패"
            recent_logs.append(log_entry)
            if len(recent_logs) > 50:
                recent_logs.pop(0)
            return JSONResponse(content=add_privacy_notice({
                "error": "법령 ID 조회 실패",
                "안내": "입력한 법령명이 정확한지 확인하거나, 아래 국가법령정보센터에서 직접 검색해 주세요.",
                "법령메인": make_article_link(law_name_full, None)
            }), status_code=404)
        res = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
            "OC": api_key,
            "target": "law",
            "type": "XML",
            "ID": law_id,
            "pIndex": 1,
            "pSize": 1000
        })
        res.raise_for_status()
        if "법령이 없습니다" in res.text:
            log_entry["status"] = "error"
            log_entry["error"] = "해당 법령은 조회할 수 없습니다."
            recent_logs.append(log_entry)
            if len(recent_logs) > 50:
                recent_logs.pop(0)
            return JSONResponse(content=add_privacy_notice({
                "error": "해당 법령은 조회할 수 없습니다.",
                "법령메인": make_article_link(law_name_full, None)
            }), status_code=403)
        article_no_norm = normalize_article_no(article_no)
        내용, 조문전체, available_articles, canonical_article_no, 구조화 = extract_article_with_full(
            res.text, article_no_norm, clause_no, subclause_no, law_name_full
        )
        law_url = make_article_link(law_name_full, canonical_article_no or article_no_norm)
        markdown = make_markdown_table(
            law_name_full, canonical_article_no or article_no_norm,
            clause_no, subclause_no, 내용, law_url, 조문전체, available_articles
        )
        result = {
            "source": "api",
            "출처": "lawService+HTMLfallback+구조화",
            "법령명": law_name_full,
            "조문": f"{canonical_article_no or article_no_norm}" if article_no else "",
            "항": f"{clause_no}항" if clause_no else "",
            "호": f"{subclause_no}호" if subclause_no else "",
            "내용": 내용,
            "조문전체": 조문전체,
            "구조화": 구조화,  # 항/호/가지조문 자동 분리 구조
            "법령링크": law_url,
            "markdown": markdown,
            "조문목록": available_articles
        }
        log_entry["status"] = "success"
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
        print("🚨 API 에러:", e)
        return JSONResponse(content=add_privacy_notice({
            "error": "API 호출 실패",
            "에러내용": str(e)
        }), status_code=500)

@app.get("/test-log", summary="최근 요청 로그 10건 조회")
@app.head("/test-log")
def test_log():
    return add_privacy_notice({"recent_logs": recent_logs[-10:]})
# === [추가 1] 법령목록조회서비스 (LawListService) ===
@app.get("/law-list", summary="법령목록조회서비스(LawListService)")
def get_law_list(
    query: Optional[str] = Query(None, example="학교폭력"),
    law_cls: Optional[str] = Query(None, description="법령구분코드(예: 001)", example="001"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    params = {
        "OC": API_KEY,
        "target": "law",
        "type": "XML",
        "pIndex": page,
        "pSize": page_size,
    }
    if query:
        params["query"] = query
    if law_cls:
        params["displayCls"] = law_cls
    res = requests.get("https://www.law.go.kr/DRF/lawSearch.do", params=params)
    res.raise_for_status()
    data = xmltodict.parse(res.text)
    return add_privacy_notice(data)

# === [추가 2] 조문목록조회서비스 (ArticleListService) ===
@app.get("/article-list", summary="조문목록조회서비스(ArticleListService)")
def get_article_list(
    law_id: str = Query(..., example="52413"),
    type: str = Query("XML", description="XML 또는 JSON"),
):
    params = {
        "OC": API_KEY,
        "target": "article",
        "type": type.upper(),
        "ID": law_id,
        "pIndex": 1,
        "pSize": 1000
    }
    res = requests.get("https://www.law.go.kr/DRF/articleList.do", params=params)
    res.raise_for_status()
    if type.upper() == "JSON":
        return add_privacy_notice(res.json())
    else:
        return add_privacy_notice(xmltodict.parse(res.text))

# === [추가 3] 조문상세조회서비스 (ArticleService) ===
@app.get("/article-detail", summary="조문상세조회서비스(ArticleService)")
def get_article_detail(
    law_id: str = Query(..., example="52413"),
    article_seq: str = Query(..., example="1084544"),
    type: str = Query("XML", description="XML 또는 JSON"),
):
    params = {
        "OC": API_KEY,
        "target": "article",
        "type": type.upper(),
        "ID": law_id,
        "articleSeq": article_seq
    }
    res = requests.get("https://www.law.go.kr/DRF/articleService.do", params=params)
    res.raise_for_status()
    if type.upper() == "JSON":
        return add_privacy_notice(res.json())
    else:
        return add_privacy_notice(xmltodict.parse(res.text))

# === [추가 4] 통합 스키마 안내 (예시) ===
openapi_schemas = {
    "law-list": {
        "법령목록": [
            {
                "법령ID": "52413",
                "법령명한글": "학교폭력예방 및 대책에 관한 법률 시행령",
                "법령약칭명": "...",
                "공포일자": "YYYYMMDD",
                "시행일자": "YYYYMMDD",
                # ... 기타 필드
            }
        ]
    },
    "article-list": {
        "조문목록": [
            {
                "조문ID": "1084544",
                "조문번호": "제14조의3",
                "조문제목": "...",
                "조문구분": "...",
                # ... 기타
            }
        ]
    },
    "article-detail": {
        "조문상세": {
            "조문ID": "1084544",
            "조문번호": "제14조의3",
            "조문제목": "...",
            "조문내용": "...",
            # ... 기타
        }
    }
}

@app.get("/schema", summary="통합 스키마/예시")
def get_openapi_schema():
    """
    통합 서비스 스키마(예시) 안내
    """
    return add_privacy_notice(openapi_schemas)
