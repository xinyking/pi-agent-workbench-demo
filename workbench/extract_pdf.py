"""Bounded PDF text extraction worker; pypdf is an optional dependency."""
import json
import resource
import sys


def extract(path):
    from pypdf import PdfReader
    reader=PdfReader(path,strict=False)
    if reader.is_encrypted and not reader.decrypt(''):raise ValueError('PDF 已加密，请上传解密后的文件')
    if len(reader.pages)>300:raise ValueError('PDF 超过 300 页，请按章节拆分')
    pages=[];total=0
    for page in reader.pages:
        text=page.extract_text(extraction_mode='layout') or ''
        total+=len(text)
        if total>2*1024*1024:raise ValueError('PDF 解析文字过多，请拆分文档')
        pages.append(text)
    return pages


if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(35,35))
    try:
        if sys.argv[1].lower().endswith('.docx'):
            from .document_parsers import extract_docx
            result=extract_docx(sys.argv[1])
        else:
            from importlib.metadata import version
            pages=extract(sys.argv[1]);empty=[i+1 for i,text in enumerate(pages) if not text.strip()]
            warnings=['基础文字提取；多栏顺序、图表和公式需对照原 PDF。']
            if empty:warnings.append('以下页未提取到文字，可能需要 OCR：'+','.join(map(str,empty)))
            result={'pages':pages,'parser':'pypdf-'+version('pypdf'),'warnings':warnings,'empty_pages':empty}
        print(json.dumps(result,ensure_ascii=False))
    except Exception as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
