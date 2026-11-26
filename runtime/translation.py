"""Work out which language a ticket opener writes in and translate ticket messages.

Translation comes from Google Translate's public web endpoints, which need no key or
account. They are free but unofficial, so every caller treats "no answer" as normal.
"""

import re
import unicodedata
from collections import deque, namedtuple
from time import monotonic

import requests

Reading = namedtuple('Reading', 'language english')
Reply = namedtuple('Reply', 'text label')

# Each endpoint takes several texts in one request and reports the language of each.
_BATCH_ENDPOINTS = (
    ('https://translate.googleapis.com/translate_a/t', 'gtx'),
    ('https://clients5.google.com/translate_a/t', 'dict-chrome-ex'),
    ('https://translate.google.com/translate_a/t', 'dict-chrome-ex'),
)
PER_MINUTE = 60
_paused_until = {}
_recent = deque(maxlen=PER_MINUTE)

MAX_ITEMS = 40
MAX_CHARACTERS = 4000
STRONG_EVIDENCE = 3

_LANGUAGE = re.compile(r'[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?\Z')
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
_PROTECTED = re.compile(
    r'(?s:```.*?(?:```|\Z))'                           # code blocks
    r'|`[^`\n]+`'                                      # inline code
    r'|<a?:\w+:\d+>|<[@#][!&]?\d+>|<t:\d+(?::\w)?>'    # Discord emoji, mentions and timestamps
    r'|https?://\S+|www\.\S+'                          # links
    # Each of the rest can only start at the beginning of a word, so that a very long run of
    # letters, dots or dashes is looked at once instead of once from every position in it.
    r'|(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+'       # email addresses
    r'|(?<![\w/\\])(?:[A-Za-z]:)?[/\\][\w.-]+(?:[/\\][\w.-]+)+'   # file paths
    r'|(?<![\w.$-])[A-Za-z_][\w$-]*(?:\.[\w$-]+)*\.(?:jar|js|ts|py|json|ya?ml|txt|log|zip|sh|properties|toml|cfg|conf'
    r'|env|html|css|php|java|sql|db|xml|ini|exe|bat|png|jpe?g|gif)\b'   # file names
    r'|(?<![\w.$])[A-Za-z_]\w*(?:\.[A-Za-z_$][\w$]*){2,}'           # dotted names such as java.lang.Error
    r'|(?<![\w.-])(?=[\w.-]*[A-Za-z])(?=[\w.-]*[\d_])\w+(?:[.-]\w+)*'   # names and versions with digits or underscores
)
_PLACEHOLDER = re.compile(r'<@\s*(\d+)\s*>')
_LOG_LINE = re.compile(
    r'\s*(?:\[[^\]]*(?:\d{1,2}:\d{2}|INFO|WARN|ERROR|DEBUG|FATAL|thread|/)[^\]]*\]|\d{1,4}[-/:]\d{1,2}[-/:]\d{1,4}'
    r'|at\s+[\w.$<>]+\(|Caused by:|Traceback|File "|npm (?:ERR|WARN)|[\w.$]*(?:Exception|Error)\b\s*:'
    r'|(?:ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL)\s*[:|\]])')
# A message for another bot, such as "$close" or "!rename new-name".
_COMMAND = re.compile(r'\s*[!$%^&./=+?~-][A-Za-z]\S*(?:\s+\S+)?\s*\Z')
_CODE_CHARACTERS = frozenset('[]{}()<>=/\\|:;_#$%^&*+~`@"')
_BLANK_LINES = re.compile(r'\n{3,}')

# Words that are English wherever they appear. Two of them make a line English even
# when it also holds names, typos or a few words of another language.
_STRONG = frozenset("""
the and you your yours are were been being have having had does did doing done don't doesn't didn't can't cannot
couldn't won't wouldn't shouldn't isn't aren't wasn't weren't haven't hasn't hadn't i'm i've i'll i'd it's that's what's
there's here's he's she's they're you're we're let's who's would should could might must shall this that these those
there their theirs they them then than what when where which while who whom whose why how with without within about
above after again against because before between both but during each every from further here into itself just more
most much myself not now off once only other our ours out over own same some such through too under until very yet any
anything anyone everything everyone something someone nothing nobody please thanks thank hello sorry still already
always never really maybe though although however since his her him she
""".split())

_ENGLISH = _STRONG | frozenset("""
a an in on no me so do as at to by up my was will has want help is of or we he be if am it its us for i oh ok okay yes
get got getting go going goes went gone come came coming make made making take took taken taking give gave given tell
told say said saying see saw seen look try tried trying use using work know knew known think thought find found need
ask seem feel leave left call keep kept let begin began start stop run ran running show shown hear heard play move live
believe bring brought happen write wrote written sit stand lose lost pay paid meet include continue set learn change
lead understand understood watch follow create speak spoke read spend spent grow open walk win won offer remember love
consider appear buy bought wait serve die send sent expect build built stay fall fell cut reach kill remain suggest
raise pass sell sold require report decide pull push return explain hope develop carry break broke broken receive
agree support hit produce eat cover catch caught draw choose chose chosen cause point listen realize place close add
check fix solve resolve restart reboot reinstall install uninstall delete remove upload download update upgrade
downgrade connect disconnect join login log logged logout sign register verify reset renew suspend unsuspend ban kick
crash lag load save backup restore boot enable disable allow deny block click press type enter copy paste select switch
edit rename refresh reload respond reply replied answer appreciate apologize confirm contact claim earn redeem transfer
purchase refund cancel cancelled charge expire exist fail error issue problem bug server bot node panel dashboard
console terminal file folder directory disk storage ram memory cpu core port ip address domain subdomain dns database db
mysql sql host hosting website site web page link url api key token password username user name email mail account
profile credit coin money payment plan tier free paid premium trial price cost bill billing invoice store shop ticket
staff admin owner member team mod moderator question message chat channel discord minecraft java bedrock nodejs python
script code plugin modpack version egg jar world map player game lobby proxy network connection internet wifi vpn
firewall ssl certificate ssh sftp ftp client app application software program system windows linux mac android phone pc
computer laptop browser chrome screen screenshot image picture photo video line status time day week month year hour
minute second today yesterday tomorrow tonight morning night moment thing stuff way people person man guy friend reason
result option setting config configuration setup step guide tutorial doc documentation info information detail data
limit usage space size number amount list kind part case end side top bottom right middle front back good bad great
nice fine sure wrong true false new old big small large little long short high low full empty fast slow quick easy hard
simple different similar another next last first third previous current recent latest early late able unable available
unavailable possible impossible online offline active inactive public private closed correct incorrect valid invalid
ready busy down working dead alive real fake main important whole entire single double many less least few several
enough also quite pretty actually basically literally probably perhaps definitely certainly exactly almost nearly
sometimes often usually ever even soon later everywhere anywhere somewhere nowhere away together alone instead
otherwise anyway whether either neither all none hi hey heyy hii yo sup thx ty tysm tyvm thnx pls plz kk k yeah yea yep
yup ye ya nope nah lol lmao lmfao rofl xd haha hahaha hehe bro bruh dude mate sir guys fam buddy pal idk idc ik ikr imo
tbh ngl fr rn btw nvm np yw gg wp ez gl hf afk brb omg omfg wtf wth tf smh fyi asap aka dm pm ping tag u ur r y yall
gonna wanna gotta gimme lemme kinda sorta dunno cuz cus coz bc tho thru sry srry mb wsg hru hbu wyd wdym wym gn gm bye
goodbye cya ttyl welcome wow oops ugh hmm hm uh um ah aw yay damn dang heck hell shit fuck fucking crap suck dont doesnt
didnt cant wont wouldnt shouldnt couldnt isnt arent wasnt werent havent hasnt hadnt im ive id youre youve youll theyre
theyve thats whats theres heres lets hes shes weve aint mine yourself themselves ourselves himself herself
internal external occur process request response module thread bind bound native method class function object string
value null undefined exception failure warning timeout refuse permission access forbidden unauthorized missing exit
signal listen init initialize container docker volume mount daemon wings pterodactyl nest allocation allocate startup
command variable environment env path root home chmod sudo apt pip npm yarn package dependency library import export
compile deploy deployment release branch commit git github repo repository clone merge fabric forge paper spigot bukkit
purpur velocity bungeecord waterfall vanilla modded modloader datapack resource pack texture skin whitelist operator
gamemode survival creative difficulty seed chunk tps latency ms kb gb tb byte bit percent uptime downtime maintenance
outage incident region location usa eu europe asia attempt unknown unexpected invalid argument
parameter syntax runtime stack trace heap overflow corrupt corrupted retry later soon queue pending approve approval
deny review manual automatic translate translation language english speak spoken understand bedwars
skyblock smp pvp hub spawn rank perm perms donate donation boost booster giveaway invite role roles emoji react
can like one two three four five six seven eight nine ten well may put mean meant turn around along near far else ago lot
bit best better worse worst cool awesome amazing perfect weird strange normal random sudden stuck freeze froze frozen
hang hung shut shutdown power forgot forget forgotten notice mention note attach share post provide accept reject
decline hold held pause resume proceed finish complete trouble solution workaround assistance service customer control
feature tool button menu tab window form field box text word letter title subject topic content talk kid school house
life country city state group family due activity inactivity total max min maximum minimum average extra additional
rather prefer wish guess suppose matter care mind wonder curious confused confusing clear unclear sense idea clue fault
mistake accident purpose manually automatically self per via etc anymore everytime whenever wherever whatever
therefore thus meanwhile afterwards currently recently fully partly mostly mainly simply totally completely
absolutely seriously honestly hopefully luckily sadly finally eventually immediately instantly quickly slowly
""".split())

# Chat shorthand staff use, spelled out so it is translated as what it means.
_SHORTHAND = {
    'u': 'you', 'ur': 'your', 'pls': 'please', 'plz': 'please', 'thx': 'thanks', 'thnx': 'thanks', 'ty': 'thank you',
    'tysm': 'thank you so much', 'tyvm': 'thank you very much', 'np': 'no problem', 'yw': "you're welcome",
    'idk': "I don't know", 'brb': 'be right back', 'rn': 'right now', 'lmk': 'let me know', 'nvm': 'never mind',
    'wdym': 'what do you mean', 'wym': 'what do you mean', 'btw': 'by the way', 'imo': 'in my opinion',
    'tbh': 'to be honest', 'ig': 'I guess', 'gonna': 'going to', 'wanna': 'want to', 'gotta': 'have to',
    'ofc': 'of course', 'hru': 'how are you', 'cuz': 'because', 'cus': 'because', 'coz': 'because', 'bc': 'because',
    'tho': 'though', 'smth': 'something', 'sth': 'something', 'ppl': 'people', 'prob': 'probably',
    'prolly': 'probably', 'dunno': "don't know", 'lemme': 'let me', 'gimme': 'give me', 'kinda': 'kind of',
    'sorta': 'sort of', 'msg': 'message', 'acc': 'account', 'pw': 'password', 'asap': 'as soon as possible',
    'atm': 'at the moment', 'ik': 'I know', 'sry': 'sorry', 'srry': 'sorry', 'kk': 'okay', 'thru': 'through',
    'im': "I'm", 'ive': "I've", 'dont': "don't", 'doesnt': "doesn't", 'didnt': "didn't", 'cant': "can't",
    'wont': "won't", 'isnt': "isn't", 'thats': "that's", 'whats': "what's", 'youre': "you're",
}

LANGUAGES = {
    'af': 'Afrikaans', 'sq': 'Albanian', 'am': 'Amharic', 'ar': 'Arabic', 'hy': 'Armenian', 'az': 'Azerbaijani',
    'eu': 'Basque', 'be': 'Belarusian', 'bn': 'Bengali', 'bs': 'Bosnian', 'bg': 'Bulgarian', 'ca': 'Catalan',
    'yue': 'Cantonese', 'ceb': 'Cebuano', 'ny': 'Chichewa', 'zh': 'Chinese', 'zh-CN': 'Chinese (Simplified)',
    'zh-TW': 'Chinese (Traditional)', 'co': 'Corsican', 'hr': 'Croatian', 'cs': 'Czech', 'da': 'Danish',
    'nl': 'Dutch', 'en': 'English', 'eo': 'Esperanto', 'et': 'Estonian', 'tl': 'Filipino', 'fil': 'Filipino',
    'fi': 'Finnish', 'fr': 'French', 'fy': 'Frisian', 'gl': 'Galician', 'ka': 'Georgian', 'de': 'German',
    'el': 'Greek', 'gu': 'Gujarati', 'ht': 'Haitian Creole', 'ha': 'Hausa', 'haw': 'Hawaiian', 'iw': 'Hebrew',
    'he': 'Hebrew', 'hi': 'Hindi', 'hmn': 'Hmong', 'hu': 'Hungarian', 'is': 'Icelandic', 'ig': 'Igbo',
    'id': 'Indonesian', 'ga': 'Irish', 'it': 'Italian', 'ja': 'Japanese', 'jw': 'Javanese', 'jv': 'Javanese',
    'kn': 'Kannada', 'kk': 'Kazakh', 'km': 'Khmer', 'rw': 'Kinyarwanda', 'ko': 'Korean', 'ku': 'Kurdish',
    'ky': 'Kyrgyz', 'lo': 'Lao', 'la': 'Latin', 'lv': 'Latvian', 'lt': 'Lithuanian', 'lb': 'Luxembourgish',
    'mk': 'Macedonian', 'mg': 'Malagasy', 'ms': 'Malay', 'ml': 'Malayalam', 'mt': 'Maltese', 'mi': 'Maori',
    'mr': 'Marathi', 'mn': 'Mongolian', 'my': 'Burmese', 'ne': 'Nepali', 'no': 'Norwegian', 'or': 'Odia',
    'ps': 'Pashto', 'fa': 'Persian', 'pl': 'Polish', 'pt': 'Portuguese', 'pt-PT': 'Portuguese (Portugal)',
    'pa': 'Punjabi', 'ro': 'Romanian', 'ru': 'Russian', 'sm': 'Samoan', 'gd': 'Scots Gaelic', 'sr': 'Serbian',
    'st': 'Sesotho', 'sn': 'Shona', 'sd': 'Sindhi', 'si': 'Sinhala', 'sk': 'Slovak', 'sl': 'Slovenian',
    'so': 'Somali', 'es': 'Spanish', 'su': 'Sundanese', 'sw': 'Swahili', 'sv': 'Swedish', 'tg': 'Tajik',
    'ta': 'Tamil', 'tt': 'Tatar', 'te': 'Telugu', 'th': 'Thai', 'tr': 'Turkish', 'tk': 'Turkmen', 'uk': 'Ukrainian',
    'ur': 'Urdu', 'ug': 'Uyghur', 'uz': 'Uzbek', 'vi': 'Vietnamese', 'cy': 'Welsh', 'xh': 'Xhosa', 'yi': 'Yiddish',
    'yo': 'Yoruba', 'zu': 'Zulu',
}


def language_name(code):
    return LANGUAGES.get(code) or LANGUAGES.get(str(code).split('-')[0]) or str(code).upper()


def valid_language(code):
    return isinstance(code, str) and bool(_LANGUAGE.match(code)) and code.lower() != 'en'


def _post(url, params, data):
    now = monotonic()
    if now < _paused_until.get(url, 0):
        return None
    if len(_recent) == PER_MINUTE and now - _recent[0] < 60:
        # A flood of messages must not use up what the free endpoints allow everyone else.
        return None
    _recent.append(now)
    pause = 60
    try:
        response = requests.post(url, params=params, data=data, headers={'User-Agent': 'Mozilla/5.0'}, timeout=(3, 8))
        if response.status_code == 200:
            return response.json()
        if response.status_code in (403, 429):
            # Too many requests from this address: leave the endpoint alone for a while.
            pause = 900
    except (requests.RequestException, ValueError):
        pass
    _paused_until[url] = monotonic() + pause
    return None


def translate_batch(texts, target, source='auto'):
    """[(translation, language)] for each text in order, or None when no endpoint answers."""
    for url, client in _BATCH_ENDPOINTS:
        answer = _post(url, [('client', client), ('sl', source), ('tl', target)], [('q', text) for text in texts])
        pairs = []
        for item in answer if isinstance(answer, list) else ():
            if isinstance(item, str):
                pairs.append((item, source))
            elif isinstance(item, list) and item and isinstance(item[0], str):
                pairs.append((item[0], item[1] if len(item) > 1 and isinstance(item[1], str) else source))
        # Anything but one answer per text means the endpoint did not understand the request.
        if len(pairs) == len(answer or ()) == len(texts):
            return pairs
    return None


def _words(text):
    return [word.lower() for word in _WORD.findall(text.replace('\u2019', "'"))]


def _plain(word):
    """Without accents, so "caido" and "ca\u00eddo" compare equal."""
    return ''.join(char for char in unicodedata.normalize('NFD', word) if not unicodedata.combining(char))


def _is_english(word):
    if word in _ENGLISH:
        return True
    for suffix, replacement in (("'s", ''), ('s', ''), ('es', ''), ('ed', ''), ('ed', 'e'), ('d', ''), ('ing', ''),
                                ('ing', 'e'), ('ly', ''), ('er', ''), ('est', ''), ('ies', 'y'), ('ied', 'y')):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[:-len(suffix)]
            if stem + replacement in _ENGLISH or (stem[-1] == stem[-2] and stem[:-1] in _ENGLISH):
                return True
    return False


def _latin(word):
    return all(ord(char) < 0x250 or 0x1E00 <= ord(char) <= 0x1EFF for char in word)


def _weight(word):
    """How much a word says about the language: 0 for keyboard mashing, more for long unspaced scripts."""
    if _latin(word):
        plain = unicodedata.normalize('NFD', word)
        if not any(char in 'aeiouy' for char in plain) or (len(word) >= 4 and len(set(word)) <= 2):
            return 0
        return 1
    first = ord(word[0])
    # Chinese, Japanese, Thai, Lao, Khmer and Burmese are written without spaces between words.
    if (0x3040 <= first <= 0x30FF or 0x3400 <= first <= 0x9FFF or first >= 0x20000 or 0x0E00 <= first <= 0x0EFF
            or 0x1000 <= first <= 0x109F or 0x1780 <= first <= 0x17FF):
        return max(1, len(word) // 2)
    return 1


def _mask(text, spans):
    def keep(match):
        spans.append(match.group(0))
        return f'<@{len(spans) - 1}>'
    return _PROTECTED.sub(keep, text)


def _placeholders(text):
    return [int(index) for index in _PLACEHOLDER.findall(text)]


def _unmask(text, spans, expected):
    """Put protected text back, appending any piece the translation dropped."""
    seen = set()

    def restore(match):
        index = int(match.group(1))
        if index >= len(spans):
            return match.group(0)
        seen.add(index)
        return spans[index]
    text = _PLACEHOLDER.sub(restore, text.replace('\u200b', ''))
    missing = [spans[index] for index in expected if index not in seen]
    return ' '.join([text] + missing) if missing else text


def _same(first, second):
    return ([char for char in first.casefold() if char.isalnum()]
            == [char for char in second.casefold() if char.isalnum()])


def _technical(masked, spans):
    """A pasted log or code line: program output, not something the person wrote."""
    if _LOG_LINE.match(_unmask(masked, spans, ())):
        return True
    plain = _PLACEHOLDER.sub('', masked)
    letters = sum(char.isalpha() for char in plain)
    return letters < sum(char.isdigit() or char in _CODE_CHARACTERS for char in plain)


def _line_words(masked):
    return _words(_PLACEHOLDER.sub(' ', masked))


def _foreign_part(masked, minimum=2):
    """(start, end) of the longest stretch of a partly English line that is not English, or None."""
    matches = list(_WORD.finditer(masked.replace('\u2019', "'")))
    unknown = [index for index, match in enumerate(matches) if not _is_english(match.group(0).lower())]
    best = None
    run = []
    for index in unknown + [None]:
        # One English word between two foreign ones does not end the stretch.
        if run and (index is None or index - run[-1] > 2):
            if (len(run) >= minimum or not _latin(matches[run[0]].group(0))) and (best is None or len(run) > len(best)):
                best = run
            run = []
        if index is not None:
            run.append(index)
    if best is None:
        return None
    return matches[best[0]].start(), matches[best[-1]].end()


class _Item:
    """One piece of a message sent for translation: a whole line, or the foreign part of an English one."""

    def __init__(self, line_index, masked, start=0, end=None):
        self.line_index = line_index
        self.masked = masked
        self.start = start
        self.end = len(masked) if end is None else end
        self.text = masked[self.start:self.end]
        self.translation = self.text
        self.language = 'en'

    def foreign_words(self):
        return [word for word in _words(self.text) if not _is_english(word) and _weight(word)]

    def replaced_words(self):
        """The words the translation actually turned into something else."""
        translated = set(_words(self.translation))
        return [word for word in self.foreign_words() if word not in translated]

    def evidence(self):
        return sum(_weight(word) for word in self.replaced_words())


def _survives_round_trip(items, language):
    """Whether translating the English back gives the person's own words again.

    Two words of slang, a name or a typo can come back "translated" from some language;
    unlike real writing, they do not come back as themselves.
    """
    back = translate_batch([item.translation for item in items], language, 'en')
    returned = {_plain(word) for translation, _ in back or () for word in _words(translation)}
    return all(_plain(word) in returned for item in items for word in item.replaced_words())


def interpret(text, known=None):
    """What a ticket opener wrote, read as their own language.

    Returns Reading(language, english) when the message holds real writing in a language
    other than English. Returns None for English, pasted logs and code, keyboard mashing,
    a message too short to tell, and when no translation endpoint answers.
    `known` is the language already found for this ticket.
    """
    text = str(text or '')
    if len(text) > 2 * MAX_CHARACTERS:
        # Of a huge paste, read the start and the end: that is where people add their own words.
        start = text[:MAX_CHARACTERS]
        text = start + ('\n```\n' if start.count('```') % 2 else '\n') + text[-MAX_CHARACTERS:]
    spans = []
    lines = _mask(text, spans).split('\n')
    shown = set()
    items = []
    size = 0
    for index, masked in enumerate(lines):
        words = _line_words(masked)
        technical = _technical(masked, spans)
        code = any(spans[span].startswith('```') for span in _placeholders(masked))
        if not technical and (words or not code):
            # Code and logs are left out of the English version; other lines stay as written.
            shown.add(index)
        if not words or len(items) >= MAX_ITEMS or size >= MAX_CHARACTERS:
            continue
        english = sum(_is_english(word) for word in words)
        strong = sum(word in _STRONG for word in words)
        # A pasted error can end in the person's own words: "Error: ... no se que hacer".
        part = _foreign_part(masked, 3 if technical else 2)
        if english == len(words):
            continue
        if technical or (strong >= 2 and english >= 0.6 * len(words)):
            choices = [part] if part else []
        else:
            choices = [None, part] if part and english >= 2 else [None]
        for choice in choices:
            items.append(_Item(index, masked, *(choice or ())))
            size += len(items[-1].text)
    if not items:
        return None

    answer = translate_batch([item.text for item in items], 'en')
    if answer is None:
        return None
    for item, (translation, language) in zip(items, answer):
        item.translation, item.language = translation, language
    # A line sent both whole and as its foreign part keeps whichever was really translated.
    chosen = {}
    for item in items:
        if item.line_index not in chosen or not chosen[item.line_index].evidence():
            chosen[item.line_index] = item
    items = list(chosen.values())

    totals = {}
    for item in items:
        if item.evidence():
            totals[item.language] = totals.get(item.language, 0) + item.evidence()
    language = max(totals, key=totals.get, default=None)
    if not language:
        return None
    proof = [word for item in items if item.language == language and item.evidence() for word in item.foreign_words()]
    # Writing in another alphabet cannot be English slang, a name or a typo, so less of it is enough.
    enough = STRONG_EVIDENCE if all(_latin(word) for word in proof) else 2
    if known and (totals.get(known) or totals[language] < enough):
        # Stay with the language already in use unless the opener clearly writes in another.
        language = known
    elif language == 'en':
        # Google translates Traditional Chinese but reports it as English.
        if not any(0x3400 <= ord(word[0]) <= 0x9FFF for word in proof):
            return None
        language = 'zh-TW'
    elif totals[language] < enough:
        # One word says too little. Two count when they are real words of that language.
        if totals[language] < 2 or not _survives_round_trip(
                [item for item in items if item.language == language and item.evidence()], language):
            return None
    if not valid_language(language):
        return None

    # Short lines are often read as some other language on their own: read them again as the opener's.
    strays = [item for item in items if item.language != language and item.foreign_words() and not item.evidence()
              and item.text == item.masked and len(_words(item.text)) <= 3]
    strays += [item for item in items if item.language not in (language, 'en') and item.evidence()]
    if strays:
        again = translate_batch([item.text for item in strays], 'en', language)
        for item, (translation, _) in zip(strays, again or []):
            item.translation = translation
    if not any(item.evidence() for item in items):
        return None

    english = {index: _unmask(lines[index], spans, ()) for index in shown}
    for item in items:
        if item.evidence():
            english[item.line_index] = (_unmask(item.masked[:item.start], spans, ())
                                        + _unmask(item.translation.strip(), spans, _placeholders(item.text))
                                        + _unmask(item.masked[item.end:], spans, ()))
    return Reading(language, _BLANK_LINES.sub('\n\n', '\n'.join(english[index] for index in sorted(english))).strip())


def translate_reply(text, language):
    """A staff message in the ticket opener's language.

    Returns Reply(text, label) where label is "Automatic translation" in that language,
    or None when there is nothing to translate or no endpoint answers.
    """
    text = str(text or '')
    if not valid_language(language) or _COMMAND.match(text):
        return None
    spans = []
    lines = _mask(text, spans).split('\n')
    items = []
    size = 0
    for index, masked in enumerate(lines):
        if not _line_words(masked) or _technical(masked, spans) or len(items) >= MAX_ITEMS or size >= MAX_CHARACTERS:
            continue
        items.append((index, masked))
        size += len(masked)
    if not items:
        return None
    spelled_out = [_WORD.sub(lambda match: _SHORTHAND.get(match.group(0).lower(), match.group(0)), masked)
                   for _, masked in items]
    answer = translate_batch(spelled_out + ['Automatic translation'], language, 'en')
    if answer is None:
        return None
    translated = {index: _unmask(lines[index], spans, ()) for index in range(len(lines))}
    changed = False
    for (index, masked), english, (translation, _) in zip(items, spelled_out, answer):
        changed = changed or not _same(english, translation)
        translated[index] = _unmask(translation.strip(), spans, _placeholders(masked))
    if not changed:
        return None
    return Reply('\n'.join(translated[index] for index in range(len(lines))).strip(), answer[-1][0].strip())
