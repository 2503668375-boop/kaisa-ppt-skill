"""Verify native company cover/end artwork. Interior pages are deliberately outside this check."""
import argparse
import hashlib
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def links(book, part):
    folder, name = posixpath.split(part)
    rel = posixpath.join(folder, '_rels', name + '.rels')
    if rel not in book.namelist():
        return {}
    return {e.get('Id'): (e.get('Type').split('/')[-1],
            posixpath.normpath(posixpath.join(folder, e.get('Target'))).lstrip('/'))
            for e in ET.fromstring(book.read(rel)) if e.get('TargetMode') != 'External'}


def page_art(book, part, allow_title):
    root = ET.fromstring(book.read(part))
    rels = links(book, part)
    def visible(e):
        attrs = dict(e.attrib)
        if e.tag == P + 'cNvPr':
            attrs.pop('title', None)
            attrs.pop('descr', None)
        for key, value in list(attrs.items()):
            if key.startswith(R) and value in rels:
                kind, dest = rels[value]
                attrs[key] = hashlib.sha256(book.read(dest)).hexdigest() if kind == 'image' else kind
        text = '' if allow_title and e.tag == A + 't' else (e.text or '').strip()
        return (e.tag, sorted(attrs.items()), text, [visible(c) for c in e
                if c.tag not in (P + 'extLst', A + 'extLst', P + 'custDataLst')])
    return [visible(c) for c in root.find(P + 'cSld') if c.tag in (P + 'bg', P + 'spTree')]


def signature(book, part, cover):
    layers = [page_art(book, part, cover)]
    for kind in ['slideLayout', 'slideMaster']:
        part = next(dst for relation, dst in links(book, part).values() if relation == kind)
        layers.append(page_art(book, part, False))
    return layers


def inspect(filename):
    with ZipFile(filename) as book:
        part = 'ppt/presentation.xml'
        root = ET.fromstring(book.read(part))
        rels = links(book, part)
        pages = [rels[e.get(R + 'id')][1] for e in root.find(P + 'sldIdLst')]
        if len(pages) < 2:
            raise ValueError('缺少封面或封底')
        size = root.find(P + 'sldSz')
        return (size.get('cx'), size.get('cy')), signature(book, pages[0], True), signature(book, pages[-1], False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('company_template')
    parser.add_argument('report')
    args = parser.parse_args()
    results = [x == y for x, y in zip(inspect(args.company_template), inspect(args.report))]
    for label, passed in zip(['画布', '固定封面', '固定封底'], results):
        print(label, '一致' if passed else '有差异')
    raise SystemExit(0 if all(results) else 1)
