from tech_monitoring.db.connection import get_connection

# 값싼 대량 컷 기준 — 정밀 임계값은 담당자 기준 확인 후 조정 [확인 필요]
MIN_TEXT_LENGTH = 50  # title + summary/content 합산 최소 길이
BLOCKED_EXTENSIONS = (".pdf", ".zip", ".exe", ".doc", ".docx", ".ppt", ".pptx")


def _fails_stage1(title: str, summary: str | None, content: str | None, url: str) -> str | None:
    if not title or not title.strip():
        return "empty_title"
    if any(url.lower().split("?")[0].endswith(ext) for ext in BLOCKED_EXTENSIONS):
        return "blocked_extension"
    text_len = len(title) + len(summary or "") + len(content or "")
    if text_len < MIN_TEXT_LENGTH:
        return "too_short"
    return None


def apply_stage1(batch_size: int = 1000) -> dict:
    """경량 룰 프리필터: 확장자·최소 길이·빈 제목만 값싸게 컷. 관련도는 Stage2에서."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, summary, content, url FROM articles WHERE status = 'new' LIMIT %s",
            (batch_size,),
        )
        rows = cur.fetchall()

    checked = archived = 0
    with conn.cursor() as cur:
        for article_id, title, summary, content, url in rows:
            checked += 1
            reason = _fails_stage1(title, summary, content, url)
            if reason:
                cur.execute(
                    """
                    UPDATE articles
                    SET status = 'archived',
                        importance_signals = importance_signals || jsonb_build_object('filtered_stage', 'stage1', 'reason', %s::text)
                    WHERE id = %s
                    """,
                    (reason, article_id),
                )
                archived += 1

    conn.close()
    return {"checked": checked, "archived": archived, "passed": checked - archived}


if __name__ == "__main__":
    print(apply_stage1())
