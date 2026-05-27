"""한국어 초성 검색 유틸리티"""
import re

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
    """보스 이름이 쿼리와 매칭되는지 확인 (부분일치 + 공백무시 + 초성검색)"""
    bn = boss_name.lower()
    q  = query.lower()

    if bn == q:
        return True
    if q in bn:
        return True

    # 공백 무시 비교 ("블랙릴리" ↔ "블랙 릴리" 양방향 매칭)
    bn_ns = bn.replace(' ', '')
    q_ns  = q.replace(' ', '')
    if q_ns and (bn_ns == q_ns or q_ns in bn_ns):
        return True

    boss_cs = get_chosung(boss_name)
    if is_chosung_only(q):
        return len(q) >= 3 and q in boss_cs

    # 쿼리에 단독 자음이 섞인 경우(오타) 초성-초성 매칭 제외
    query_cs = get_chosung(q)
    if query_cs and query_cs in boss_cs and not any(c in CHOSUNG_SET for c in q):
        return True

    return False


def normalize_time(raw: str) -> tuple[int, int] | None:
    """'0530' 또는 '05:30' 또는 '05:30:00' → (5, 30). 파싱 실패 시 None."""
    # HH:MM:SS → HH:MM (초 제거)
    m = re.match(r"^(\d{1,2}):(\d{2}):\d{2}$", raw)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h, mn
    raw = raw.replace(':', '')
    if len(raw) == 4 and raw.isdigit():
        h, mn = int(raw[:2]), int(raw[2:])
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h, mn
    return None
