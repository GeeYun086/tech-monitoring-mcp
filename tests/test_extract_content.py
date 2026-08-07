from tech_monitoring.collectors.extract_content import _should_skip


def test_skips_techmeme_river_page_urls():
    """Techmeme URL은 하나의 날짜별 리버 페이지를 #fragment로 가리키는 헤드라인
    앵커다. trafilatura는 fragment를 못 보고 페이지에서 아무 블록이나 골라오는데,
    실측 결과 Techmeme 15건 전부가 제목과 무관한 본문으로 오염됐었다
    (예: "Nikita Bier 퇴사" 기사에 "Meta Muse Code 출시" 본문이 들어감).
    """
    assert _should_skip("https://www.techmeme.com/260805/p52#a260805p52")
    assert _should_skip("https://techmeme.com/260805/p52#a260805p52")


def test_does_not_skip_direct_article_urls():
    """HN 등 애그리게이터는 원본 기사의 실제 URL로 링크하므로(리버 페이지가
    아님) 본문 추출을 그대로 시도해야 한다."""
    assert not _should_skip("https://blog.cloudflare.com/cloudflare-os/")
    assert not _should_skip("https://www.theverge.com/tech/975677/some-article")
