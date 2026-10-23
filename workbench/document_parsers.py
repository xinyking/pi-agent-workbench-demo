"""Bounded structural extraction; originals remain authoritative."""
import re
import zipfile
from xml.etree import ElementTree as ET

LIMIT=12000

def split_text(text, markdown=False):
    """Prefer paragraph/heading boundaries; keep fenced code and tables together."""
    blocks=[];current=[];fence=None
    for line in text.splitlines(keepends=True):
        marker=re.match(r'^\s{0,3}(`{3,}|~{3,})',line) if markdown else None
        if marker:
            token=marker.group(1)
            if fence is None:fence=(token[0],len(token))
            elif token[0]==fence[0] and len(token)>=fence[1]:fence=None
        if markdown and not fence and re.match(r'^#{1,6}\s',line) and current:
            blocks.append(''.join(current));current=[]
        current.append(line)
        if not line.strip() and fence is None:blocks.append(''.join(current));current=[]
    if current:blocks.append(''.join(current))
    chunks=[];buf=''
    for block in blocks:
        if len(buf)+len(block)>LIMIT and buf:chunks.append(buf);buf=''
        # Oversized ordinary paragraphs may split. Preserve structured blocks;
        # read() still bounds each response and provides next_offset.
        structured=markdown and (re.match(r'^\s*(`{3,}|~{3,})',block) or '|' in block)
        if len(block)>LIMIT and not structured:
            chunks.extend(block[i:i+LIMIT] for i in range(0,len(block),LIMIT));continue
        buf+=block
    if buf:chunks.append(buf)
    return chunks or ['']


def extract_docx(path):
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        files=archive.infolist()
        if len(files)>10000 or sum(f.file_size for f in files)>32*1024*1024:
            raise ValueError('Word 解压内容过大，请拆分文档')
        if any(f.flag_bits&1 for f in files):raise ValueError('Word 已加密，请先解密')
        info=archive.getinfo('word/document.xml')
        if info.file_size>8*1024*1024:raise ValueError('Word 正文过大')
        raw=archive.read(info)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('不支持带实体声明的 Word 文件')
    root=ET.fromstring(raw);body=root.find('w:body',ns)
    if body is None:raise ValueError('Word 缺少正文')
    def paragraph(element):
        output=[]
        for part in element.iter():
            tag=part.tag.rsplit('}',1)[-1]
            if tag=='t':output.append(part.text or '')
            elif tag=='tab':output.append('\t')
            elif tag in ('br','cr'):output.append('\n')
        return ''.join(output)
    blocks=[]
    for element in body:
        tag=element.tag.rsplit('}',1)[-1]
        if tag=='p':
            value=paragraph(element)
            style=element.find('w:pPr/w:pStyle',ns)
            style_name=style.get('{'+ns['w']+'}val','') if style is not None else ''
            match=re.search(r'(?:heading|标题)\s*([1-6])',style_name,re.I)
            if match:value='#'*int(match.group(1))+' '+value
            elif element.find('w:pPr/w:numPr',ns) is not None:value='- '+value
            if value.strip():blocks.append(value+'\n\n')
        elif tag=='tbl':
            rows=[]
            for row in element.findall('w:tr',ns):
                cells=[' / '.join(paragraph(p) for p in c.findall('w:p',ns)).replace('|','\\|').replace('\n',' ') for c in row.findall('w:tc',ns)]
                rows.append(cells)
            if rows:
                # Preserve row order without inventing a header row.
                blocks.append('\n'.join('| '+' | '.join(row)+' |' for row in rows)+'\n\n')
    text=''.join(blocks)
    if len(text)>2*1024*1024:raise ValueError('Word 解析文字过多，请拆分文档')
    return {'pages':split_text(text,True),'parser':'docx-xml-v1','warnings':['Word 按章节/文本块定位，不提供排版页码；图片、批注、公式、页眉页脚和复杂编号未完整提取。']}
