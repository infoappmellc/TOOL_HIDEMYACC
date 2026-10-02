from apps.dashboard.language import article_comment, article_language, video_cta


def test_comment_uses_article_language():
    url = "https://example.com/news"
    assert article_comment(url, "Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny") == (
        "Cały artykuł: https://example.com/news"
    )
    assert article_comment(url, "Das ist in der deutschen Nationalmannschaft nicht akzeptabel") == (
        "Vollständiger Artikel: https://example.com/news"
    )
    assert article_comment(url, "Article headline", "nl-NL") == "Volledig artikel: https://example.com/news"
    assert article_comment(url, "Tin tức trong ngày", "vi") == "Toàn bộ bài viết: https://example.com/news"


def test_clear_title_overrides_wrong_site_language_but_short_title_uses_metadata():
    assert article_language("Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny", "en-US") == "pl"
    assert article_language("Das ist in der deutschen Nationalmannschaft nicht akzeptabel", "en-US") == "de"
    assert article_language("Breaking news", "pl-PL") == "pl"


def test_video_cta_matches_content_language():
    assert video_cta("Tragedia nad Bałtykiem", "pl") == "Więcej szczegółów w komentarzach 👇"
    assert video_cta("Das ist in der deutschen Nationalmannschaft", "de") == (
        "Mehr Details in den Kommentaren 👇"
    )
    assert video_cta("Article headline", "nl") == "Meer details in de reacties 👇"
    assert video_cta("Article headline", "en") == "More details in the comments 👇"
