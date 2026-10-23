"""Conversation attachments with scoped, paginated source text for Pi tools."""
import base64
import binascii
import hashlib
import importlib.util
import json
import re
from .document_parsers import split_text
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

MAX_BYTES=10*1024*1024
MAX_TEXT=2*1024*1024
ALLOWED={'.pdf','.docx','.md','.txt','.tex','.csv'}


class Documents:
    def __init__(self,store):
        self.store=store
        self._threads=set();self._children=set();self._closed=False
        self.directory=store.path.parent/'uploads'
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,conversation_id TEXT,name TEXT,kind TEXT,size INTEGER,digest TEXT,status TEXT,error TEXT,created REAL,archived INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS document_metadata(document_id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS document_pages(document_id TEXT,page INTEGER,text TEXT,PRIMARY KEY(document_id,page));
            ''')
            store.db.execute("UPDATE documents SET status='interrupted',error='后台重启，请重新解析' WHERE status='processing'")

    def list(self,conversation_id):
        with self.store.lock:
            rows=self.store.db.execute('''SELECT d.*,COUNT(p.page) AS pages FROM documents d LEFT JOIN document_pages p ON p.document_id=d.id WHERE d.conversation_id=? AND d.archived=0 GROUP BY d.id ORDER BY d.created''',(conversation_id,)).fetchall()
        return [{**dict(row),**self.metadata(row['id'])} for row in rows]

    def metadata(self,document_id):
        with self.store.lock:
            row=self.store.db.execute('SELECT data FROM document_metadata WHERE document_id=?',(document_id,)).fetchone()
        return json.loads(row['data']) if row else {'parser':'legacy','warnings':[]}

    def _get(self,conversation_id,document_id):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM documents WHERE id=? AND conversation_id=?',(document_id,conversation_id)).fetchone()
        if row is None:raise ValueError('附件不存在或不属于此对话')
        return dict(row)

    def source(self,conversation_id,document_id):
        item=self._get(conversation_id,document_id)
        return self.directory/document_id/('source'+item['kind']),item

    def upload(self,conversation_id,name,encoded):
        if not isinstance(name,str) or not 1<=len(name)<=200 or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise ValueError('无效文件名')
        kind=Path(name).suffix.lower()
        if kind not in ALLOWED:raise ValueError('支持 PDF、DOCX、Markdown、TXT、TeX、CSV；旧版 DOC 请先转换为 DOCX')
        if not isinstance(encoded,str) or len(encoded)>((MAX_BYTES+2)//3)*4:raise ValueError('每个附件最多 10 MiB')
        try:data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error):raise ValueError('附件编码无效') from None
        if not data or len(data)>MAX_BYTES:raise ValueError('附件不能为空，每个最多 10 MiB')
        if kind=='.pdf':
            if b'%PDF-' not in data[:1024]:raise ValueError('文件不是有效的 PDF 格式')
        elif kind!='.docx':
            if len(data)>MAX_TEXT:raise ValueError('文本附件最多 2 MiB')
            try:text=data.decode('utf-8-sig')
            except UnicodeError:raise ValueError('文本附件请使用 UTF-8 编码') from None
            if '\x00' in text:raise ValueError('附件包含二进制内容')
        with self.store.lock:
            existing=self.store.db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM documents WHERE conversation_id=? AND archived=0',(conversation_id,)).fetchone()
            if existing[0]>=12 or existing[1]+len(data)>40*1024*1024:raise ValueError('每个对话最多 12 个附件，总计最多 40 MiB')
            document_id=uuid.uuid4().hex
            directory=self.directory/document_id;directory.mkdir(mode=0o700,parents=True)
            self.directory.chmod(0o700)
            source=directory/('source'+kind);source.write_bytes(data);source.chmod(0o600)
            with self.store.db:self.store.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',(document_id,conversation_id,name,kind,len(data),hashlib.sha256(data).hexdigest(),'processing','',time.time(),0))
        if kind in ('.pdf','.docx'):self.retry(conversation_id,document_id,initial=True)
        else:self._save_pages(document_id,split_text(text,kind=='.md'),{'parser':'markdown-blocks-v1' if kind=='.md' else 'text-blocks-v1','warnings':[]})
        return next(item for item in self.list(conversation_id) if item['id']==document_id)

    def _status(self,document_id,status,error=''):
        with self.store.lock,self.store.db:self.store.db.execute('UPDATE documents SET status=?,error=? WHERE id=?',(status,error,document_id))

    def _save_pages(self,document_id,pages,metadata=None):
        if sum(len(page) for page in pages)>MAX_TEXT:raise ValueError('解析文本超过 2 MiB，请拆分文档')
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO document_metadata VALUES(?,?)',(document_id,json.dumps(metadata or {'parser':'legacy','warnings':[]},ensure_ascii=False)))
            self.store.db.execute('DELETE FROM document_pages WHERE document_id=?',(document_id,))
            self.store.db.executemany('INSERT INTO document_pages VALUES(?,?,?)',[(document_id,index+1,text) for index,text in enumerate(pages)])
            status='ready' if any(text.strip() for text in pages) else 'no_text'
            self.store.db.execute('UPDATE documents SET status=?,error=? WHERE id=?',(status,'' if status=='ready' else '未提取到可读文字；扫描件或图片文档需要 OCR，本地基础解析尚不支持 OCR',document_id))

    def retry(self,conversation_id,document_id,initial=False):
        source,item=self.source(conversation_id,document_id)
        if item['kind'] not in ('.pdf','.docx'):raise ValueError('仅 PDF/DOCX 需要重新解析')
        if item['status']=='processing' and not initial:raise ValueError('正在解析，请稍候')
        if item['kind']=='.pdf' and importlib.util.find_spec('pypdf') is None:
            self._status(document_id,'needs_parser','PDF 已保存；当前 Python 环境未安装 pypdf，安装后点击重新解析')
            return
        self._status(document_id,'processing')
        def extract():
            process=None
            try:
                if self._closed:return
                process=subprocess.Popen([sys.executable,'-m','workbench.extract_pdf',str(source)],cwd=Path(__file__).resolve().parent.parent,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                with self.store.lock:
                    self._children.add(process)
                    if self._closed:process.terminate()
                try:output,error=process.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill();process.communicate()
                    raise ValueError('文档解析超过 45 秒，请拆分或转换后重试') from None
                if process.returncode:raise ValueError(error.strip()[-1200:] or '文档解析失败')
                result=json.loads(output)
                self._save_pages(document_id,result.pop('pages'),result)
            except Exception as exc:self._status(document_id,'interrupted' if self._closed else 'failed',str(exc)[:1500])
            finally:
                with self.store.lock:
                    self._children.discard(process)
                    self._threads.discard(threading.current_thread())
        thread=threading.Thread(target=extract,daemon=True)
        with self.store.lock:
            if self._closed:raise ValueError('工作台正在关闭')
            self._threads.add(thread)
        thread.start()

    def shutdown(self):
        with self.store.lock:
            self._closed=True
            children=list(self._children);threads=list(self._threads)
        for process in children:
            try:process.terminate()
            except OSError:pass
        for thread in threads:thread.join(timeout=3)

    def archive(self,conversation_id,document_id):
        self._get(conversation_id,document_id)
        with self.store.lock,self.store.db:self.store.db.execute('UPDATE documents SET archived=1 WHERE id=?',(document_id,))

    def read(self,conversation_id,document_id,page=1,offset=0):
        item=self._get(conversation_id,document_id)
        if item['status']!='ready':raise ValueError(item['error'] or '附件尚未解析完成')
        if any(not isinstance(n,int) or isinstance(n,bool) or n<0 for n in (page,offset)) or page<1:raise ValueError('无效页码或偏移')
        with self.store.lock:
            row=self.store.db.execute('SELECT text FROM document_pages WHERE document_id=? AND page=?',(document_id,page)).fetchone()
            count=self.store.db.execute('SELECT COUNT(*) FROM document_pages WHERE document_id=?',(document_id,)).fetchone()[0]
        if row is None:raise ValueError('页码不存在')
        text=row['text'];end=offset+12000
        return {'document_id':document_id,'name':item['name'],'page':page,'pages':count,'unit':'page' if item['kind']=='.pdf' else 'chunk','text':text[offset:end],'next_offset':end if end<len(text) else None,**self.metadata(document_id),'citation':f'[{item["name"]}，'+(f'PDF 第 {page} 页' if item['kind']=='.pdf' else f'文本块 {page}')+f'](paper:{document_id}#page={page})'}

    def outline(self,conversation_id,document_id):
        item=self._get(conversation_id,document_id)
        if item['status']!='ready':raise ValueError(item['error'] or '附件尚未就绪')
        with self.store.lock:
            rows=self.store.db.execute('SELECT page,text FROM document_pages WHERE document_id=? ORDER BY page',(document_id,)).fetchall()
        entries=[]
        for row in rows:
            headings=[line.strip()[:200] for line in row['text'].splitlines() if re.match(r'^#{1,6}\s',line)]
            entries.append({'page':row['page'],'title':' / '.join(headings)[:300] or next((line.strip()[:160] for line in row['text'].splitlines() if line.strip()),'（无文字）')})
        return {'document_id':document_id,'unit':'page' if item['kind']=='.pdf' else 'chunk','entries':entries,**self.metadata(document_id)}

    def search(self,conversation_id,document_id,query):
        self.outline(conversation_id,document_id)
        if not isinstance(query,str) or not 1<=len(query.strip())<=200:raise ValueError('搜索词需为 1–200 个字符')
        query=query.strip();matches=[]
        with self.store.lock:
            rows=self.store.db.execute('SELECT page,text FROM document_pages WHERE document_id=? ORDER BY page',(document_id,)).fetchall()
        for row in rows:
            # Literal Unicode match, not regex. Keep the returned offsets in original text.
            found=next((m for m in re.finditer(re.escape(query),row['text'],re.I)),None)
            if found:
                start=max(0,found.start()-100)
                matches.append({'page':row['page'],'offset':start,'excerpt':row['text'][start:found.end()+250], 'citation':self.read(conversation_id,document_id,row['page'],start)['citation']})
                if len(matches)>=20:break
        return {'matches':matches,'limit':20,'note':'每块返回首个匹配；无匹配不代表整篇已被模型阅读。'}

    def for_task(self,task_id):
        with self.store.lock:row=self.store.db.execute('SELECT conversation_id FROM context_snapshots WHERE task_id=? ORDER BY created DESC LIMIT 1',(task_id,)).fetchone()
        if row is None:raise ValueError('当前任务未关联附件对话')
        return row['conversation_id']

    def tool(self,conversation_id,payload):
        action=payload.get('action','list')
        if action=='list':return {'documents':self.list(conversation_id)}
        if action=='outline':return self.outline(conversation_id,payload.get('document_id'))
        if action=='search':return self.search(conversation_id,payload.get('document_id'),payload.get('query'))
        if action=='read':return self.read(conversation_id,payload.get('document_id'),payload.get('page',1),payload.get('offset',0))
        raise ValueError('附件工具支持 list、outline、search、read')
