from __future__ import annotations

import re


COMMENT_PREFIXES = {
    "pl": "Cały artykuł:",
    "de": "Vollständiger Artikel:",
    "nl": "Volledig artikel:",
    "en": "Full article:",
    "vi": "Toàn bộ bài viết:",
    "fr": "Article complet :",
    "es": "Artículo completo:",
    "pt": "Artigo completo:",
    "it": "Articolo completo:",
    "cs": "Celý článek:",
    "sk": "Celý článok:",
    "ro": "Articolul complet:",
}

VIDEO_CTA = {
    "pl": "Więcej szczegółów w komentarzach",
    "de": "Mehr Details in den Kommentaren",
    "nl": "Meer details in de reacties",
    "en": "More details in the comments",
    "vi": "Xem thêm chi tiết trong bình luận",
    "fr": "Plus de détails dans les commentaires",
    "es": "Más detalles en los comentarios",
    "pt": "Mais detalhes nos comentários",
    "it": "Maggiori dettagli nei commenti",
    "cs": "Více podrobností v komentářích",
    "sk": "Viac podrobností v komentároch",
    "ro": "Mai multe detalii în comentarii",
}

_CLUES = {
    "pl": {"jest", "nad", "morza", "ciało", "mężczyzny", "się", "nie", "dla", "przez", "który", "został", "wyłowiono"},
    "de": {"das", "ist", "der", "die", "und", "nicht", "beim", "diese", "für", "deutschen", "szene", "sorgt"},
    "nl": {"het", "een", "van", "voor", "niet", "deze", "zijn", "met", "wordt", "nieuws"},
    "en": {"the", "and", "with", "from", "this", "that", "for", "article", "news", "after"},
    "vi": {"và", "của", "trong", "được", "người", "không", "bài", "viết", "tại"},
    "fr": {"les", "des", "une", "dans", "pour", "avec", "cette", "article", "est"},
    "es": {"los", "las", "una", "para", "con", "esta", "noticia", "artículo"},
    "pt": {"uma", "para", "com", "esta", "notícia", "artigo", "não"},
    "it": {"una", "per", "con", "questa", "notizia", "articolo", "della"},
}


def article_language(text: str, declared: str = "") -> str:
    """Prefer clear article wording; otherwise trust the article's language tag."""
    declared_code = re.split(r"[-_]", declared.strip().lower(), maxsplit=1)[0]
    tokens = set(re.findall(r"[^\W\d_]+", text.casefold(), re.UNICODE))
    scores = {code: len(tokens & clues) for code, clues in _CLUES.items()}
    if re.search(r"[ąćęłńóśźż]", text.casefold()):
        scores["pl"] += 2
    if re.search(r"[äöüß]", text.casefold()):
        scores["de"] += 2
    best = max(scores, key=scores.get)
    if scores[best] >= 2 and scores[best] > max((score for code, score in scores.items() if code != best), default=0) \
            and (scores[best] > scores.get(declared_code, 0) or declared_code not in COMMENT_PREFIXES):
        return best
    return declared_code if declared_code in COMMENT_PREFIXES else (best if scores[best] >= 2 else "en")


def article_comment(source_url: str, title: str, language: str = "") -> str:
    code = article_language(title, language)
    return f"{COMMENT_PREFIXES[code]} {source_url}"


def video_cta(text: str, language: str = "") -> str:
    code = article_language(text, language)
    return f"{VIDEO_CTA[code]} 👇"
