import sys
import logging
from config import (
    validate_config,
    NOTION_TOKEN,
    NOTION_SOURCE_PAGE_ID,
    NOTION_TARGET_PAGE_ID,
    TARGET_SECTION_TITLE,
    MY_NAME,
    MY_ROLE,
    EXTRACT_MODE,
    AI_PROVIDER,
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    TIMEZONE
)
from notion_service import NotionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("NotionSync")

def run_weekly_sync():
    logger.info("==========================================")
    logger.info(f"🚀 주간 회의록 -> WorkNote 자동화 작업 시작 (모드: {EXTRACT_MODE})")
    logger.info("==========================================")

    # 1. 환경 설정 검증
    errors = validate_config()
    if errors:
        logger.error("환경 설정 오류가 발생했습니다:")
        for err in errors:
            logger.error(f"  - {err}")
        logger.error("'.env' 파일을 확인하거나 GitHub Secrets를 확인해주세요.")
        sys.exit(1)

    # 2. 서비스 초기화
    notion = NotionService(NOTION_TOKEN)
    tasks = []

    # 3. 추출 모드에 따른 할 일 수집
    if EXTRACT_MODE in ["code", "auto"]:
        logger.info(f"1단계: '마케팅팀 주간회의' 페이지({NOTION_SOURCE_PAGE_ID})에서 Control + E 코딩 텍스트 추출 중...")
        code_tasks = notion.fetch_meeting_code_tasks(NOTION_SOURCE_PAGE_ID)
        
        if code_tasks:
            logger.info(f"✨ Control + E 코딩 처리된 텍스트 {len(code_tasks)}개를 성공적으로 찾았습니다!")
            tasks = code_tasks
        else:
            if EXTRACT_MODE == "code":
                logger.warning("회의록 내에 Control + E (인라인 코드) 처리된 텍스트를 찾지 못했습니다.")
                return
            else:
                logger.info("Control + E 코딩 텍스트가 없어 AI 요약 모드로 전환합니다.")

    # auto 모드에서 코딩 텍스트가 없었거나, ai 모드인 경우
    if not tasks and EXTRACT_MODE in ["ai", "auto"]:
        logger.info(f"1단계(AI): '마케팅팀 주간회의' 페이지({NOTION_SOURCE_PAGE_ID}) 회의록 전체 가져오기...")
        notes = notion.fetch_meeting_notes(NOTION_SOURCE_PAGE_ID)
        if not notes.strip():
            logger.warning("회의록 페이지에서 텍스트 내용을 찾지 못했습니다. 작업을 종료합니다.")
            return

        preview_len = min(200, len(notes))
        logger.info(f"회의록 텍스트 조회 성공 ({len(notes)} 글자)\n--- 내용 미리보기 ---\n{notes[:preview_len]}...\n---------------------")

        logger.info(f"2단계: AI({AI_PROVIDER})를 활용하여 '{MY_NAME or '나'}'의 이번 주 할 일 추출 중...")
        from ai_service import AIService
        ai = AIService(
            provider=AI_PROVIDER,
            gemini_api_key=GEMINI_API_KEY,
            openai_api_key=OPENAI_API_KEY
        )
        tasks = ai.extract_action_items(notes, user_name=MY_NAME, user_role=MY_ROLE)

    if not tasks:
        logger.warning("추출된 할 일(Action Items)이 없습니다.")
        return

    logger.info(f"총 {len(tasks)}개의 할 일이 추출되었습니다:")
    for idx, t in enumerate(tasks, 1):
        logger.info(f"  {idx}. {t}")

    # 5. Daily Life 페이지의 WorkNote(Short-term) 표에 추가
    logger.info(f"3단계: 'Daily Life' 페이지({NOTION_TARGET_PAGE_ID})의 '{TARGET_SECTION_TITLE}' 섹션 표(Table)에 추가 중...")
    table_id, is_new = notion.find_or_create_worknote_table(
        target_page_id=NOTION_TARGET_PAGE_ID,
        section_name=TARGET_SECTION_TITLE,
        tz_name=TIMEZONE
    )

    added_count = notion.append_tasks_to_table(table_id, tasks)

    logger.info("==========================================")
    logger.info(f"🎉 성공적으로 자동화 작업이 완료되었습니다! (총 {added_count}개 추가)")
    logger.info("==========================================")

if __name__ == "__main__":
    import requests
    try:
        run_weekly_sync()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        detail = e.response.text if e.response is not None else str(e)
        logger.error("==========================================")
        logger.error(f"❌ Notion API 오류 (HTTP {status})")
        if status == 401:
            logger.error("👉 NOTION_TOKEN이 잘못되었습니다. 토큰 전체(ntn_ 또는 secret_ 포함)를 공백 없이 다시 등록해 주세요.")
        elif status in (403, 404):
            logger.error("👉 페이지를 찾을 수 없거나 권한이 없습니다.")
            logger.error("   1) 두 노션 페이지 모두 우측 상단 ··· → 연결(Connections) → 내 통합을 추가했는지 확인")
            logger.error("   2) NOTION_SOURCE_PAGE_ID / NOTION_TARGET_PAGE_ID에 '페이지' 링크를 넣었는지 확인")
        elif status == 400:
            logger.error("👉 요청 형식 오류입니다. 아래 상세 메시지를 확인해 주세요.")
        logger.error(f"상세: {detail}")
        logger.error("==========================================")
        sys.exit(1)
