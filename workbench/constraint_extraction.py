"""Conservative explicit-instruction extraction. No LLM/network calls or profiling."""
import re

PRIVATE = re.compile(r'(?i)(?:sk-[a-z0-9_-]{12,}|(?:api[_ -]?key|password|passwd|token|secret|密码|密钥|身份证|银行卡|病史|诊断)(?:[\s:=：]|是|为)|[\w.+-]{1,64}@[\w.-]{1,253}\.[a-z]{2,24}|(?<!\d)1[3-9]\d{9}(?!\d))')
OPT_OUT = re.compile(r'不要(?:记住|记录|保存)|别(?:记住|记录|保存)|不允许(?:记住|记录|保存)|do not (?:remember|store|save)|don.t (?:remember|store|save)', re.I)
DURABLE = re.compile(r'请记住|记住我的|以后|今后|始终|每次|所有任务|默认|remember|from now on|always|by default', re.I)
QUOTED = re.compile(r'文档(?:说|中|内容)|资料(?:中|内容)|以下(?:是|为).*(?:原文|资料|文档)|引用|示例|例如|假设|如果|解释这|他说|她说|(?:document|example|quoted text)\s*:', re.I)


def clauses(text):
    """Reject quoted/fenced/example material; precision takes priority over recall."""
    fenced=False
    for line in text.splitlines():
        if line.strip().startswith(('```','~~~')):fenced=not fenced;continue
        if fenced or line.lstrip().startswith('>') or QUOTED.search(line):continue
        for part in re.split(r'[。；;\n，,]',line):
            quote=part.strip(' \t-•')
            if not 4<=len(quote)<=500 or any(c in quote for c in '“”「」『』"') or '?' in quote or '？' in quote:continue
            if not PRIVATE.search(quote) and not OPT_OUT.search(quote):yield quote


def classify(quote):
    checks=[
        ('network',r'(?:禁止|不要|不得|不允许|不能|不可|不可以|可以|允许|需要|必须)?(?:联网|上网)|(?:只|仅)(?:使用|用|读取)本地|(?:no|without|allow|use) (?:internet|network)'),
        ('budget',r'预算.{0,8}(?:不超过|不能超过|上限|最多|改为|改成|调整为|为|是|<=|≤).{0,3}\d|budget.{0,12}\d'),
        ('language',r'(?:输出|回答|交付|回复|报告).{0,8}(?:中文|英文|英语|Chinese|English)|(?:使用|用)(?:中文|英文|英语)|(?:respond|reply|answer).{0,10}(?:Chinese|English)'),
        ('deadline',r'(?:截止|最迟|完成时间|deadline|due by).{0,30}(?:\d|今天|明天|周|星期)'),
        ('format',r'(?:输出|交付|报告|格式).{0,12}(?:Markdown|JSON|CSV|表格)|(?:使用|用)(?:Markdown|JSON|CSV|表格)'),
    ]
    for key,pattern in checks:
        if re.search(pattern,quote,re.I):return key
    if re.search(r'必须|不得|禁止|不要|请勿|只允许|仅限|\bmust\b|\bnever\b',quote,re.I):return 'constraint'
    return None


def extract(text):
    if OPT_OUT.search(text):return []
    return [{'key':key,'quote':quote} for quote in clauses(text) if (key:=classify(quote))]
