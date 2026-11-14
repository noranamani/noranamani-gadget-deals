"""Conservative, title-only exclusions for obvious accessory/category mismatches.

Never infer specifications or mutate historical observations. Keep ambiguous
titles for review. Exclusion happens before ranking and history append.
"""
from __future__ import annotations
import re


def is_relevant(category_id: str, title: str) -> bool:
    if not isinstance(title, str) or not title.strip():
        return False
    s = title.casefold()
    has = lambda pattern: bool(re.search(pattern, s, re.IGNORECASE))

    if category_id == "usb-charger":
        # Car socket adapters are not the AC charging bricks of this category.
        return not has(r"シガーソケット|カーチャージャー|車載充電器|12v車|24v車")
    if category_id == "wireless-charger":
        # A wired-only wall charger must not appear in Qi/Qi2 comparisons.
        wired = has(r"ac充電器|acアダプター|usb[-ー ]?[ac]x?1|コンセント充電器")
        wireless = has(r"ワイヤレス|無線充電|qi2?|magsafe|マグセーフ")
        return not (wired and not wireless)
    if category_id == "microsd":
        # Reader-only product; never reject a memory card merely because a
        # conversion adapter is included.
        reader = has(r"カードリーダー|カードライター|メモリカードリーダー")
        card = has(r"(?:micro\s*sd|マイクロ\s*sd)\s*(?:カード|メモリーカード)")
        return not (reader and not card)
    if category_id == "car-charger":
        return not has(r"usb[-ー ]?c\s*から\s*シガーソケット|シガーソケット\s*メス|電源専用\s*変換アダプター")
    if category_id == "mobile-router":
        return not has(r"追加チャージ専用|チャージ専用|ギガ追加専用|"
                       r"(?:専用.{0,15})?(?:レザー|保護|収納)\s*ケース|"
                       r"(?:ルーター|wifi|wi-fi).{0,20}(?:専用ケース|保護フィルム単品)")
    return True
