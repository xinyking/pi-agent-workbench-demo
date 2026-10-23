"""Small synthetic documents, no external files or model calls."""
import io
import zipfile


def docx_bytes():
    xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>研究方法</w:t></w:r></w:p>
    <w:p><w:r><w:t>样本量为 120，结论需要核对原始资料。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>实验组</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>60</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>局限</w:t></w:r></w:p><w:p><w:r><w:t>未进行长期随访。</w:t></w:r></w:p>
    </w:body></w:document>'''
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml',xml)
    return data.getvalue()


def pdf_bytes(texts=('Sample size 120',)):
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    ids=[]
    for text in texts:
        page_id=len(objects)+1;ids.append(page_id)
        stream=('BT /F1 12 Tf 72 720 Td ('+text+') Tj ET').encode('ascii') if text else b''
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>'.encode())
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    objects[1]=('<< /Type /Pages /Kids ['+' '.join(f'{i} 0 R' for i in ids)+f'] /Count {len(ids)} >>').encode()
    data=bytearray(b'%PDF-1.4\n');offsets=[0]
    for index,body in enumerate(objects,1):
        offsets.append(len(data));data.extend(f'{index} 0 obj\n'.encode()+body+b'\nendobj\n')
    start=len(data);data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
    return bytes(data)
