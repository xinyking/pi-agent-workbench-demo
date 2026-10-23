import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workbench.documents import Documents
from workbench.store import Store

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'state.sqlite3')
        self.documents=Documents(self.store)
    def tearDown(self):self.documents.shutdown();self.store.db.close();self.temp.cleanup()
    def upload(self,name,text):return self.documents.upload('conversation-one',name,base64.b64encode(text.encode() if isinstance(text,str) else text).decode())
    def test_upload_read_scope_citation_and_pagination(self):
        doc=self.upload('研究笔记.md','# 方法\n样本量 120。'+'A'*14000)
        self.assertEqual(doc['status'],'ready')
        self.assertGreaterEqual(doc['pages'],2)
        page=self.documents.read('conversation-one',doc['id'],1)
        self.assertIn('样本量 120',page['text'])
        self.assertIn('paper:'+doc['id'],page['citation'])
        self.assertEqual(page['unit'],'chunk')
        with self.assertRaises(ValueError):self.documents.read('other',doc['id'])
        with self.assertRaises(ValueError):self.documents.read('conversation-one',doc['id'],0)
        self.assertTrue(self.documents.read('conversation-one',doc['id'],2)['text'])
        source,_=self.documents.source('conversation-one',doc['id'])
        self.assertEqual(source.stat().st_mode&0o777,0o600)
    def test_pdf_upload_never_claims_parsed_without_parser(self):
        with patch('workbench.documents.importlib.util.find_spec',return_value=None):doc=self.upload('paper.pdf',b'%PDF-1.7\nnot-yet-parsed')
        self.assertEqual(doc['status'],'needs_parser')
        with self.assertRaisesRegex(ValueError,'pypdf'):self.documents.read('conversation-one',doc['id'])
    def test_invalid_filenames_types_and_binary_are_rejected(self):
        for name,data in [('../secret.txt','x'),('paper.exe','x'),('fake.pdf','not a pdf'),('binary.txt',b'\xff'),('zero.txt','')]:
            with self.subTest(name=name),self.assertRaises(ValueError):self.upload(name,data)
        with self.assertRaises(ValueError):self.documents.upload('conversation-one','x.txt','not base64?')
    def test_archive_keeps_existing_citation_source(self):
        doc=self.upload('paper.txt','关键证据')
        self.documents.archive('conversation-one',doc['id'])
        self.assertEqual(self.documents.list('conversation-one'),[])
        self.assertEqual(self.documents.read('conversation-one',doc['id'])['text'],'关键证据')
    def test_empty_text_is_visible_as_unreadable(self):
        doc=self.upload('empty.txt','   ')
        self.assertEqual(doc['status'],'no_text')

    @unittest.skipUnless(importlib.util.find_spec('pypdf'),'optional pypdf is not installed')
    def test_real_pdf_parser_extracts_a_text_page(self):
        from workbench.extract_pdf import extract
        objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
        stream=b'BT /F1 12 Tf 72 720 Td (Sample size 120) Tj ET'
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
        data=bytearray(b'%PDF-1.4\n');offsets=[0]
        for index,body in enumerate(objects,1):
            offsets.append(len(data));data.extend(f'{index} 0 obj\n'.encode()+body+b'\nendobj\n')
        start=len(data);data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
        for offset in offsets[1:]:data.extend(f'{offset:010d} 00000 n \n'.encode())
        data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
        path=Path(self.temp.name)/'actual.pdf';path.write_bytes(data)
        pages=extract(path)
        self.assertEqual(len(pages),1)
        self.assertIn('Sample size 120',pages[0])

    def settled(self,doc):
        import time
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            result=next(d for d in self.documents.list('conversation-one') if d['id']==doc['id'])
            if result['status']!='processing':return result
            time.sleep(.02)
        self.fail('document parser did not settle')

    def test_docx_structure_search_and_original(self):
        from fixtures.document_samples import docx_bytes
        data=docx_bytes();doc=self.settled(self.upload('研究.docx',data))
        self.assertEqual(doc['status'],'ready',doc)
        self.assertEqual(doc['parser'],'docx-xml-v1')
        self.assertTrue(doc['warnings'])
        result=self.documents.read('conversation-one',doc['id'])
        self.assertIn('# 研究方法',result['text']);self.assertIn('| 实验组 | 60 |',result['text'])
        self.assertEqual(result['unit'],'chunk');self.assertNotIn('PDF 第',result['citation'])
        self.assertIn('研究方法',self.documents.tool('conversation-one',{'action':'outline','document_id':doc['id']})['entries'][0]['title'])
        search=self.documents.tool('conversation-one',{'action':'search','document_id':doc['id'],'query':'120'})
        self.assertIn('样本量',search['matches'][0]['excerpt'])
        with self.assertRaises(ValueError):self.documents.search('other',doc['id'],'120')
        self.assertEqual(self.documents.search('conversation-one',doc['id'],'[literal]')['matches'],[])
        with self.assertRaises(ValueError):self.documents.search('conversation-one',doc['id'],'')
        path,_=self.documents.source('conversation-one',doc['id']);self.assertEqual(path.read_bytes(),data)

    def test_invalid_docx_keeps_original_and_reports_failure(self):
        doc=self.settled(self.upload('broken.docx',b'not a zip archive'))
        self.assertEqual(doc['status'],'failed')
        self.assertTrue(self.documents.source('conversation-one',doc['id'])[0].exists())

    def test_markdown_fences_and_tables_survive_chunking(self):
        text='# 开始\n\n'+'第一章内容。'*1800+'\n\n## 代码\n\n```python\n'+('print(120)\n'*1500)+'```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n'
        doc=self.upload('结构.md',text)
        with self.store.lock:
            chunks=[r['text'] for r in self.store.db.execute('SELECT text FROM document_pages WHERE document_id=? ORDER BY page',(doc['id'],))]
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(c.count('```') in (0,2) for c in chunks))
        self.assertIn('| A | B |\n|---|---|\n| 1 | 2 |',chunks[-1])
        self.assertEqual(doc['parser'],'markdown-blocks-v1')

    @unittest.skipUnless(importlib.util.find_spec('pypdf'),'optional pypdf is not installed')
    def test_pdf_partial_text_warns_about_missing_page(self):
        from fixtures.document_samples import pdf_bytes
        doc=self.settled(self.upload('mixed.pdf',pdf_bytes(('Sample size 120',''))))
        self.assertEqual(doc['status'],'ready',doc);self.assertEqual(doc['empty_pages'],[2])
        self.assertTrue(any('OCR' in w for w in doc['warnings']))
        self.assertIn('120',self.documents.search('conversation-one',doc['id'],'120')['matches'][0]['excerpt'])
        empty=self.settled(self.upload('scan.pdf',pdf_bytes(('',))))
        self.assertEqual(empty['status'],'no_text');self.assertIn('OCR',empty['error'])

    def test_old_document_rows_without_metadata_remain_readable(self):
        doc=self.upload('old.txt','original')
        with self.store.lock,self.store.db:self.store.db.execute('DELETE FROM document_metadata WHERE document_id=?',(doc['id'],))
        self.assertEqual(self.documents.read('conversation-one',doc['id'])['parser'],'legacy')

    def test_docx_entity_declarations_are_rejected_without_resolving(self):
        import io,zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY x SYSTEM "file:///never-read">]><x>&x;</x>')
        doc=self.settled(self.upload('entities.docx',data.getvalue()))
        self.assertEqual(doc['status'],'failed');self.assertIn('实体声明',doc['error'])
