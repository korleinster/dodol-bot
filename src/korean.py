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
    """보스 이름이 쿼리와 매칭되는지 확인 (부분일치 + 공백무시 + 초성검색 + 단어 첫글자)"""
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

    # 단어 첫 글자 조합 매칭 ("오크" → "오염된 크루마")
    words = boss_name.split()
    if len(words) >= 2:
        initials = ''.join(w[0].lower() for w in words if w)
        if q_ns == initials:
            return True

    # 초성 전용 쿼리: 보스 초성 앞부터만 매칭
    boss_cs = get_chosung(boss_name)
    if is_chosung_only(q):
        return len(q) >= 2 and boss_cs.startswith(q)

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
