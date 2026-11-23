import os
import re
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

def clean_notion_id(raw_id: str) -> str:
    """
    Notion 페이지 URL이나 다양한 포맷의 ID에서 유효한 32자리 hex ID를 추출합니다.
    예:
    - https://www.notion.so/My-Page-2b9921e6955a40a8a68b7dc3f639ea07
    - 2b9921e6-955a-40a8-a68b-7dc3f639ea07
    - 2b9921e6955a40a8a68b7dc3f639ea07
    """
    if not raw_id:
        return ""
    
    # URL 쿼리스트링 제거
    cleaned = raw_id.split("?")[0].split("#")[0].strip()
    
    # URL 경로의 마지막 세그먼트 추출
    last_segment = cleaned.rstrip("/").split("/")[-1]
    
    # 8-4-4-4-12 UUID 포맷 탐색
    uuid_match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", last_segment)
    if uuid_match:
        return uuid_match.group(1).replace("-", "")

    # 마지막 세그먼트의 끝부분 32자리 hex (예: page-title-1234567890abcdef1234567890abcdef)
    end_hex_match = re.search(r"([0-9a-fA-F]{32})$", last_segment)
    if end_hex_match:
        return end_hex_match.group(1)

    # 구분자(-, _, 슬래시) 뒤에 오는 32자리 hex 탐색
    sep_match = re.search(r"(?:^|[-_/])([0-9a-fA-F]{32})(?:$|[-_/])", last_segment)
    if sep_match:
        return sep_match.group(1)
        
    return last_segment.replace("-", "")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_SOURCE_PAGE_ID = clean_notion_id(os.getenv("NOTION_SOURCE_PAGE_ID", ""))
NOTION_TARGET_PAGE_ID = clean_notion_id(os.getenv("NOTION_TARGET_PAGE_ID", ""))
TARGET_SECTION_TITLE = os.getenv("TARGET_SECTION_TITLE", "WorkNote(Short-term)").strip()

MY_NAME = os.getenv("MY_NAME", "").strip()
MY_ROLE = os.getenv("MY_ROLE", "마케터").strip()

# 추출 모드: 'code' (Control+E 코딩 처리 텍스트만 추출), 'ai' (AI 요약), 'auto' (코딩 텍스트 우선, 없으면 AI)
EXTRACT_MODE = os.getenv("EXTRACT_MODE", "code").lower().strip()

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower().strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul").strip()

def validate_config():
    errors = []
    if not NOTION_TOKEN:
        errors.append("NOTION_TOKEN이 설정되지 않았습니다.")
    if not NOTION_SOURCE_PAGE_ID:
        errors.append("NOTION_SOURCE_PAGE_ID(마케팅팀 주간회의 페이지 ID)가 설정되지 않았습니다.")
    if not NOTION_TARGET_PAGE_ID:
        errors.append("NOTION_TARGET_PAGE_ID(Daily Life 페이지 ID)가 설정되지 않았습니다.")
    
    # 'code' 모드일 때는 AI 키가 없어도 됨
    if EXTRACT_MODE in ["ai"]:
        if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
            errors.append("AI_PROVIDER가 'gemini'이지만 GEMINI_API_KEY가 설정되지 않았습니다.")
        elif AI_PROVIDER == "openai" and not OPENAI_API_KEY:
            errors.append("AI_PROVIDER가 'openai'이지만 OPENAI_API_KEY가 설정되지 않았습니다.")
    elif EXTRACT_MODE == "auto":
        # auto 모드인데 키가 없으면 경고만 남길 수 있게 유연하게 처리
        pass
        
    return errors
