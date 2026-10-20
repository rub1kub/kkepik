"""Bounded, local RU/EN chat moderation. No third-party message processing.

NFKC/NFKD, case folding and confusable folding follow the techniques described
in Unicode UTS #39; this application-specific vocabulary is not UTS conformance.
Strict lexical moderation cannot infer intent or cover every possible cipher.
"""
from __future__ import annotations

import re
import unicodedata as ud
import json
from pathlib import Path

_CONFUSABLES = json.loads((Path(__file__).parent/'data/chat_confusables.json').read_text())['mapping']

MAX_MESSAGE_LENGTH = 200
_RU = dict(zip('абвгдежзийклмнопрстуфхцчшщыэюя',
               ('a','b','v','g','d','e','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','sh','sch','y','e','yu','ya')))
_RU.update({'ё':'e','ь':'','ъ':'','і':'i','ї':'i','є':'e','ґ':'g'})
_VISUAL = str.maketrans({'a':'а','b':'в','c':'с','e':'е','h':'н','k':'к','m':'м',
                        'n':'п','o':'о','p':'р','t':'т','x':'х','y':'у','u':'и','r':'я','w':'ш'})
_LATIN_VISUAL = str.maketrans({'а':'a','в':'b','с':'c','е':'e','к':'k','м':'m','н':'h','о':'o','р':'p','т':'t','у':'y','х':'x','і':'i'})
_EXTRA = str.maketrans({'α':'a','β':'b','ϐ':'b','ε':'e','ϵ':'e','η':'h','ι':'i','κ':'k','ο':'o',
                       'ρ':'p','ϱ':'p','τ':'t','υ':'u','χ':'x','ν':'v','ς':'s','σ':'s','ϲ':'c',
                       'ӏ':'l','Ӏ':'l','ԁ':'d','ԍ':'g','ԛ':'q','ԝ':'w','ѕ':'s','љ':'л',
                       '🅰':'a','🅱':'b','🅾':'o','🅿':'p'})
_LEET = [str.maketrans({'0':'o','1':'i','2':'z','3':'e','4':'a','5':'s','6':'b','7':'t','8':'b','9':'g','@':'a','$':'s','!':'i'}),
         str.maketrans({'0':'o','1':'i','2':'z','3':'z','4':'ch','5':'s','6':'b','7':'t','8':'v','9':'ya','@':'a','$':'s','!':'i'})]
# Mask only entire legitimate words before joining separators. Never allow a
# message merely because it contains an innocent word next to a blocked one.
_SAFE = re.compile(
    r'^(?:(?:pod|pere)?strahu(?:yu|yus|yusya|i|y|ysya|isya|yte|ite|ytes|ites|et|etsya|esh|eshsya|em|emsya|ete|etes|yut|yutsya)|'
    r'(?:upo|po|pereupo)treblya(?:t(?:sya)?|yu|et|esh|ete|em|yut|l[iao]?|i|y|ite|yte)?|'
    r'(?:oslab|uglub)lya(?:t(?:sya)?|yu|et|esh|ete|em|yut|l[iao]?|i|y|ite|yte)?|'
    r'(?:korabl|rubl)(?:ya|yu|i|ey|em|yam|yami|yah)?|ansamblya|'
    r'zhidkost(?:i|yu|ey|yam|yami|yah)?|zhidk(?:iy|aya|oe|ie|ih|oy|uyu|ogo|omu|om)|'
    r'blyash(?:ka|ki|ku|ek|kami|kam|kah|koy)|cocktail(?:s)?|scunthorpe|'
    r'(?:na|ot|pri|do|po|pere|ras|za|vy)?pish(?:u|i|ite|ites|itesya|esh|eshsya|et|ete|em|ut|utsya)|'
    r'(?:s|vy|na|pere|po)?shi(?:t|to|ta|ty|te|li|l|la|lo|te|tes|togo|tym)|'
    r'nazvanie|nazvaniya|nazvaniyu|nazvaniem|nazvaniy|'
    r'nazyva(?:t(?:sya)?|etsya|yut|yu|esh|ete|em|l[iao]?)|hudozhnik(?:i|a|u|om|ov)?)$'
)
# Stem families cover inflections and common transcriptions. Compact forms
# catch spaces, punctuation, zero-width characters, repeats and emoji inserts.
_RU_BLOCK = re.compile(
    r'(?:p[iye]?[sz]d|pizd|pe?zd|pid[oa]r|ped[oa]r|mud[ao]k|mudil|g[ao]nd[oa]n|'
    r'sh[liyu]+[hx]|shlyu[hx]|blya|bl[ij]a|ebal|eban|ebat|ebnut|ebuch|eblan|eblo|ebash|'
    r'yebal|yeban|yebat|zaeb|vyeb|naeb|poeb|pereeb|ueb|otsos|zalup|hui|huy|huj|'
    r'hu[yi]n|hue|huyar|huyak|huyov|hueva|xui|xuy|xuj|xy[yi]|khui|khuy|'
    r'suk[aiouey]|suchk|(?:zh|j)op|her(?:nya|nyu|ney|ov|ovina)|svoloch|derm[oae]|zasran|ublyud|ubliud|debil|idiot)'
)
_EN_BLOCK = re.compile(r'(?:f[uoaxv]ck|fuk|fck|phu?c?k|shit|sh1t|shyt|bitch|biatch|cunt|asshole|'
                       r'motherf|wanker|bastard|bullshit|slut|whore|twat)')
_EN_WORD = re.compile(r'^(?:fick|ass|dick(?:s|head|heads)?|cock(?:s|sucker|suckers)?|pussy|prick|fag(?:s|got|gots)?|retard(?:ed|s)?)$')
_NAZI = re.compile(r'(?:nats[iy]z|nats[iy]st|natsik|nac[iy]z|nac[iy]st|naz[iy](?:sm|st|z|s)?|'
                   r'fas[hc](?:i[szk]|ist)|fascis|hitler|gitler|adolf|heil|heyl|hayl|hail|'
                   r'sieg|sig(?:hail|he[iy]l)|zig(?:hai|hei|haj)|swastik|svastik|'
                   r'hakenkreuz|whitepower|whitepride|whitesuprem|bloodandhonou?r|blutundehre|'
                   r'kill(?:all|the)?(?:jews|blacks)|death(?:to)?jews|gas(?:the)?jews|smert(?:vsem)?evrey|hohol|katsap|nigger|nigga|niger|zhid(?:y|ov|am|a|ok)?|churk)')
_CODE = re.compile(r'(?<!\d)(?:1488|14(?:\s*[/\\:._-]\s*|\s+)88)(?!\d)|(?<!\d)1[\W_]+4[\W_]+8[\W_]+8(?!\d)|[卐卍🖕ᛋᛊᛟ]|ϟϟ')
_MASK = re.compile(r'(?<![a-zа-я])(?:(?:на|по|за|na|po|za)?(?:(?:[хxh]|kh)[\W_]{1,8}[йиyij]|[пp][\W_]{1,8}[зzs][дd]|[бb][лl][\W_]{1,8}[тдtd]|[еe][\W_]{1,8}[аa][тлtl]|f[\W_]{1,8}(?:ck|k)|sh[\W_]{1,8}t|bi[\W_]{1,8}ch))')
_URL = re.compile(r'(?:https?\s*[:：]|www\s*[.。]|t\s*[.。]\s*me\s*/|[a-z0-9-]+\.(?:ru|com|org|net|io|me)\b)',re.I)


def clean_message(value):
    if not isinstance(value,str) or len(value)>800:
        return None
    # Remove bidi/format controls from the displayed text as well. Emoji ZWJ
    # stays for display, but is ignored by the filter below.
    value = ud.normalize('NFC',value)
    value = ''.join(c for c in value if (ud.category(c)[0]!='C' or c in '\n\t\u200d'))
    value = ' '.join(value.split())
    return value if 1<=len(value)<=MAX_MESSAGE_LENGTH and any(ud.category(c)[0] not in ('C','M','Z') for c in value) else None


def _fold(value):
    value=ud.normalize('NFKD',ud.normalize('NFKC',value).casefold())
    # Regional indicator letters, mathematical fonts, combining accents.
    value=''.join(str(ud.decimal(c)) if ud.category(c)=='Nd' else chr(ord('a')+ord(c)-0x1F1E6) if 0x1F1E6<=ord(c)<=0x1F1FF else c
                  for c in value if ud.category(c)[0] not in ('M','C'))
    value=value.translate(_EXTRA)
    return ''.join(_CONFUSABLES.get(c,c) for c in value)


def _latin(value):
    return ''.join(_RU.get(c,c) for c in value)


def blocked_reason(value):
    folded=_fold(value)
    if _CODE.search(folded) or _MASK.search(folded):
        return 'content'
    if re.fullmatch(r'[\W_]*(?:8[\W_]*8|zig|sig)[\W_]*',folded):
        return 'content'
    if _URL.search(folded):
        return 'links'
    # Exempt complete legitimate words before alternate alphabet folds.
    folded=re.sub(r'[\w]+',lambda match:' ' if _SAFE.fullmatch(_latin(match.group())) else match.group(),folded)
    views=set()
    for leet in _LEET+[{}]:
        decoded=folded.translate(leet)
        for variant in (decoded, decoded.translate(_VISUAL), decoded.translate(_LATIN_VISUAL)):
            variant=_latin(variant)
            views.add(variant)
            views.add(re.sub(r'([a-z])\1+',r'\1',variant))
    for variant in views:
        tokens=re.findall(r'[a-z0-9]+',variant)
        tokens=[t for t in tokens if not _SAFE.fullmatch(t)]
        if any(_EN_WORD.fullmatch(t) for t in tokens):
            return 'content'
        if any(_RU_BLOCK.search(t) or _EN_BLOCK.search(t) or _NAZI.search(t) for t in tokens):
            return 'content'
        # Join small pieces only as whole blocked expressions. Searching an
        # unbounded concatenation would incorrectly flag 'finish it' as 'shit'.
        for start in range(len(tokens)):
            compact=''
            for token in tokens[start:start+12]:
                if len(token)>12 or len(compact)+len(token)>40:
                    break
                compact+=token
                match=_RU_BLOCK.match(compact) or _EN_BLOCK.match(compact) or _NAZI.match(compact)
                if match or _EN_WORD.fullmatch(compact):
                    return 'content'
        # Short swears and coded slogans may be spaced out deliberately.
        if re.search(r'\b(?:eb|ebu|ebet|ebem|ebesh|ss|hh)\b',variant) or ''.join(tokens) in ('eb','ebu','ebet','ebem','ebesh','ss','hh','dick','cock','ass','fag','fak'):
            return 'content'
    return None


def safe_name(value):
    name=clean_message(value[:60]) if isinstance(value,str) else None
    return name if name and not blocked_reason(name) else 'Участник'
