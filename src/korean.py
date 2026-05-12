"""한국어 초성 검색 유틸리티"""

CHOSUNG = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
CHOSUNG_SET = set(CHOSUNG)


def get_chosung(text: str) -> str:
    result = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            result.append(CHOSUNG[(code - 0xAC00) // 588])
        elif ch in CHOSUNG_SET:
            result.append(ch)
    return ''.join(result)


def is_chosung_only(text: str) -> bool:
    return bool(text) and all(c in CHOSUNG_SET for c in text)


def boss_matches(boss_name: str, query: str) -> bool:
    """보스 이름이 쿼리와 매칭되는지 확인 (부분일치 + 초성검색)"""
    bn = boss_name.lower()
    q  = query.lower()

    if bn == q:
        return True
    if q in bn:
        return True

    boss_cs = get_chosung(boss_name)
    if is_chosung_only(q):
        return q in boss_cs

    query_cs = get_chosung(q)
    if query_cs and query_cs in boss_cs:
        return True

    return False


def normalize_time(raw: str) -> tuple[int, int] | None:
    """'0530' 또는 '05:30' → (5, 30). 파싱 실패 시 None."""
    raw = raw.replace(':', '')
    if len(raw) == 4 and raw.isdigit():
        h, m = int(raw[:2]), int(raw[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    return None
