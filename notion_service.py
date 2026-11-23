import time
import requests
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python < 3.9 fallback
    from pytz import timezone as ZoneInfo


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class NotionService:
    NOTION_API_BASE = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"
    MIN_REQUEST_INTERVAL = 0.35  # Notion API 권장 속도 제한 (초당 약 3회)
    MAX_RETRIES = 5

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json"
        }
        self._last_request_time = 0.0

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.NOTION_API_BASE}/{endpoint.lstrip('/')}"

        for attempt in range(self.MAX_RETRIES + 1):
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)

            self._last_request_time = time.monotonic()
            resp = requests.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=30,
            )

            if resp.status_code == 429 and attempt < self.MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                if not retry_after:
                    try:
                        retry_after = resp.json().get("additional_data", {}).get("retry_after")
                    except Exception:
                        retry_after = None
                wait_sec = float(retry_after) + 1.0 if retry_after else 5.0
                logger.warning(f"⏳ Notion API 요청 제한(429) 도달 - {wait_sec:.0f}초 대기 후 재시도 ({attempt + 1}/{self.MAX_RETRIES})...")
                time.sleep(wait_sec)
                continue

            if not resp.ok:
                logger.error(f"Notion {method} {endpoint} 실패 ({resp.status_code}): {resp.text}")
                resp.raise_for_status()
            return resp.json()

        resp.raise_for_status()
        return {}

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("GET", endpoint, params=params)

    def _post(self, endpoint: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", endpoint, json_data=json_data)

    def _patch(self, endpoint: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PATCH", endpoint, json_data=json_data)

    def get_block_children(self, block_id: str) -> List[Dict[str, Any]]:
        """블록의 모든 자식 블록을 페이징 처리하여 가져옵니다."""
        blocks = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"blocks/{block_id}/children", params=params)
            blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return blocks

    # API로 하위 내용을 읽을 수 없거나 읽을 필요가 없는 블록 타입
    SKIP_CHILDREN_TYPES = {"unsupported", "child_database", "link_to_page", "ai_block", "transcription", "meeting_notes"}

    def safe_get_child_blocks(self, block: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        하위 블록 조회 중 오류(예: 노션 AI 회의록 블록 등 API 미지원 블록)가 나도
        전체 작업이 멈추지 않도록 경고만 남기고 건너뜁니다.
        """
        if block.get("type") in self.SKIP_CHILDREN_TYPES:
            return []
        try:
            return self.get_block_children(block["id"])
        except requests.HTTPError as e:
            logger.warning(f"하위 블록({block.get('type')}) 조회를 건너뜁니다: {e}")
            return []

    def extract_plain_text(self, rich_text_list: List[Dict[str, Any]]) -> str:
        """rich_text 배열에서 평문 문자열 추출"""
        if not rich_text_list:
            return ""
        return "".join([t.get("plain_text", "") for t in rich_text_list]).strip()

    def get_block_text_representation(self, block: Dict[str, Any], depth: int = 0) -> str:
        """단일 블록의 텍스트 표현 추출"""
        btype = block.get("type", "")
        indent = "  " * depth
        content = ""

        if btype in block and "rich_text" in block[btype]:
            content = self.extract_plain_text(block[btype]["rich_text"])
        elif btype == "child_page":
            content = f"[하위 페이지] {block.get('child_page', {}).get('title', '')}"

        if not content:
            return ""

        if btype.startswith("heading_"):
            return f"\n{indent}### {content}\n"
        elif btype == "to_do":
            checked = "[x]" if block[btype].get("checked") else "[ ]"
            return f"{indent}- {checked} {content}"
        elif btype in ["bulleted_list_item", "numbered_list_item"]:
            return f"{indent}- {content}"
        elif btype == "quote":
            return f"{indent}> {content}"
        elif btype == "callout":
            return f"{indent}💡 {content}"
        elif btype == "toggle":
            return f"{indent}▼ {content}"
        else:
            return f"{indent}{content}"

    def extract_full_text_recursive(self, block_id: str, depth: int = 0, max_depth: int = 3,
                                    blocks: Optional[List[Dict[str, Any]]] = None) -> str:
        """하위 블록(토글 등)까지 재귀적으로 탐색하여 전체 회의록 텍스트 추출"""
        if blocks is None:
            blocks = self.get_block_children(block_id)
        lines = []

        for block in blocks:
            text = self.get_block_text_representation(block, depth)
            if text:
                lines.append(text)

            # 토글이나 하위 목록 등 자식 블록이 있는 경우 재귀 탐색
            if block.get("has_children") and depth < max_depth and block.get("type") != "child_page":
                children = self.safe_get_child_blocks(block)
                if children:
                    child_text = self.extract_full_text_recursive(block["id"], depth + 1, max_depth, blocks=children)
                    if child_text.strip():
                        lines.append(child_text)

        return "\n".join(lines)

    def fetch_meeting_notes(self, page_id: str) -> str:
        """
        '마케팅팀 주간회의' 페이지에서 회의록 텍스트를 읽어옵니다.
        만약 페이지 내에 이번 주 회의록 하위 페이지(child_page)가 있다면 가장 최신 페이지를 자동 감지합니다.
        """
        logger.info(f"회의록 페이지({page_id}) 조회 시작...")
        blocks = self.get_block_children(page_id)

        # 1. 하위 페이지가 있는지 확인 (주차별로 하위 페이지를 생성하는 워크플로우 지원)
        child_pages = [b for b in blocks if b.get("type") == "child_page"]
        if child_pages:
            # 가장 최근에 생성/수정된 하위 페이지를 선택
            latest_child_page = child_pages[-1]
            page_title = latest_child_page.get("child_page", {}).get("title", "하위 페이지")
            logger.info(f"하위 회의록 페이지 감지: '{page_title}' (ID: {latest_child_page['id']})")
            notes = self.extract_full_text_recursive(latest_child_page["id"])
            if notes.strip():
                return f"[회의록 제목: {page_title}]\n\n{notes}"

        # 2. 하위 페이지가 아니거나 단일 페이지 내에 누적 작성되는 경우
        notes = self.extract_full_text_recursive(page_id)
        return notes

    def extract_inline_codes_from_block(self, block: Dict[str, Any]) -> List[str]:
        """
        단일 블록에서 사용자가 Control + E (인라인 코드) 처리한 텍스트 또는 코드 블록 내용 추출
        """
        btype = block.get("type", "")
        extracted = []

        # 1. rich_text를 가지는 모든 블록의 annotations.code 확인
        if btype in block and "rich_text" in block[btype]:
            for rt in block[btype]["rich_text"]:
                if rt.get("annotations", {}).get("code") is True:
                    text = rt.get("plain_text", "").strip()
                    if text:
                        extracted.append(text)

        # 2. 코드 블록(Code block)인 경우도 지원
        elif btype == "code":
            code_text = "".join([t.get("plain_text", "") for t in block.get("code", {}).get("rich_text", [])]).strip()
            if code_text:
                for line in code_text.split("\n"):
                    clean_line = line.strip()
                    if clean_line:
                        extracted.append(clean_line)

        return extracted

    def extract_inline_codes_recursive(self, block_id: str, depth: int = 0, max_depth: int = 3) -> List[str]:
        """하위 블록(토글 등)까지 재귀 탐색하여 Control + E 코딩 처리된 텍스트 목록 추출"""
        blocks = self.get_block_children(block_id)
        codes = []

        for block in blocks:
            found = self.extract_inline_codes_from_block(block)
            codes.extend(found)

            if block.get("has_children") and depth < max_depth:
                child_codes = self.extract_inline_codes_recursive(block["id"], depth + 1, max_depth)
                codes.extend(child_codes)

        return codes

    def fetch_meeting_code_tasks(self, page_id: str) -> List[str]:
        """
        '마케팅팀 주간회의' 페이지(또는 최신 하위 회의록 페이지)에서
        Control + E 코딩 처리된 텍스트들만 추출하여 목록으로 반환합니다.
        """
        logger.info(f"회의록 페이지({page_id})에서 Control + E 코딩 처리된 텍스트 추출 중...")
        blocks = self.get_block_children(page_id)

        # 1. 하위 페이지가 있는 경우 최신 하위 페이지 탐색
        child_pages = [b for b in blocks if b.get("type") == "child_page"]
        if child_pages:
            latest_child_page = child_pages[-1]
            page_title = latest_child_page.get("child_page", {}).get("title", "하위 페이지")
            logger.info(f"최신 하위 회의록 페이지 감지: '{page_title}' (ID: {latest_child_page['id']})")
            return self.extract_inline_codes_recursive(latest_child_page["id"])

        # 2. 단일 페이지인 경우
        return self.extract_inline_codes_recursive(page_id)

    def find_or_create_worknote_table(self, target_page_id: str, section_name: str, tz_name: str = "Asia/Seoul") -> Tuple[str, bool]:
        """
        'Daily Life' 페이지 내에서 WorkNote(Short-term) 섹션을 찾고,
        그 아래에 있는 1열 table 블록 ID를 반환하거나 새로 생성합니다.
        
        반환값: (table_block_id, is_newly_created)
        """
        logger.info(f"Daily Life 페이지({target_page_id})에서 '{section_name}' 검색 중...")
        blocks = self.get_block_children(target_page_id)

        section_index = -1
        section_block_id = None

        for idx, block in enumerate(blocks):
            btype = block.get("type", "")
            text = ""
            if btype in block and "rich_text" in block[btype]:
                text = self.extract_plain_text(block[btype]["rich_text"])

            if section_name.lower().replace(" ", "") in text.lower().replace(" ", ""):
                section_index = idx
                section_block_id = block["id"]
                logger.info(f"섹션 '{section_name}' 발견: 블록 ID {section_block_id}")
                break

        # 섹션 발견 후 그 바로 아래에 이미 table 블록이 있는지 확인
        if section_index != -1:
            for next_idx in range(section_index + 1, min(section_index + 4, len(blocks))):
                candidate = blocks[next_idx]
                if candidate.get("type") == "table":
                    logger.info(f"기존 테이블 블록 발견: ID {candidate['id']}")
                    return candidate["id"], False

        # 테이블이 없으면 새로 생성
        tz = ZoneInfo(tz_name)
        now_str = datetime.now(tz).strftime("%Y-%m-%d")
        table_title = f"이번 주 할 일 ({now_str})"

        logger.info(f"'{section_name}' 섹션에 새 1열 테이블 생성 중...")
        new_table_block = {
            "type": "table",
            "table": {
                "table_width": 1,
                "has_column_header": True,
                "has_row_header": False
            },
            "children": [
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [
                                {
                                    "type": "text",
                                    "text": {"content": table_title},
                                    "annotations": {"bold": True}
                                }
                            ]
                        ]
                    }
                }
            ]
        }

        # section_block_id가 있으면 그 자식 또는 페이지 하단에 추가
        created_block = self._post(f"blocks/{target_page_id}/children", {"children": [new_table_block]})
        results = created_block.get("results", [])
        if results:
            new_table_id = results[0]["id"]
            logger.info(f"새 테이블 생성 완료: ID {new_table_id}")
            return new_table_id, True

        raise RuntimeError("테이블 생성에 실패했습니다.")

    def append_tasks_to_table(self, table_id: str, tasks: List[str]) -> int:
        """
        테이블 블록에 1열 table_row들을 추가합니다.
        """
        if not tasks:
            logger.warning("추가할 할 일 항목이 없습니다.")
            return 0

        logger.info(f"테이블({table_id})에 {len(tasks)}개의 할 일 행 추가 시작...")
        
        # Notion API는 1회 요청당 최대 100개 블록 허용
        BATCH_SIZE = 50
        total_added = 0

        for i in range(0, len(tasks), BATCH_SIZE):
            batch = tasks[i:i + BATCH_SIZE]
            children_rows = []
            for task in batch:
                clean_task = task.strip().lstrip("-").lstrip("•").lstrip("*").strip()
                children_rows.append({
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": clean_task
                                    }
                                }
                            ]
                        ]
                    }
                })

            self._post(f"blocks/{table_id}/children", {"children": children_rows})
            total_added += len(children_rows)

        logger.info(f"총 {total_added}개의 할 일 행이 테이블에 성공적으로 추가되었습니다.")
        return total_added
