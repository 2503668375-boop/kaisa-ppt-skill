"""核对公司模板的画布、封面和封底。可另核对原题名框内的三项业务文字。"""
import argparse
import hashlib
import posixpath
import re
import unicodedata
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


def title_shapes(root):
    """The three company covers use a title placeholder or a shape named 标题 2."""
    for shape in root.iter(P + 'sp'):
        props = shape.find(P + 'nvSpPr')
        placeholder = props.find(P + 'nvPr/' + P + 'ph')
        name = props.find(P + 'cNvPr').get('name', '')
        if ((placeholder is not None and placeholder.get('type') in ('title', 'ctrTitle'))
                or re.fullmatch(r'(?:标题|Title)\s*\d*', name, re.IGNORECASE)):
            if shape.find(P + 'txBody') is not None:
                yield shape


def title_bodies(root):
    return (shape.find(P + 'txBody') for shape in title_shapes(root))


def title_frame(root):
    shapes = list(title_shapes(root))
    tree = root.find(P + 'cSld/' + P + 'spTree')
    if len(shapes) == 1 and shapes[0] in list(tree):
        return shapes[0].find(P + 'spPr/' + A + 'xfrm')


def allow_downward_growth(original, revised, canvas_height):
    # Compare everything as before, except a permitted extension of the lower edge.
    old, new = title_frame(original), title_frame(revised)
    if old is None or new is None or old.get('rot', '0') != '0':
        return
    old_ext, new_ext = old.find(A + 'ext'), new.find(A + 'ext')
    offset = old.find(A + 'off')
    if old_ext is None or new_ext is None or offset is None:
        return
    height = int(old_ext.get('cy'))
    bottom_limit = canvas_height - int(offset.get('y'))
    if 0 < height <= int(new_ext.get('cy')) <= bottom_limit:
        new_ext.set('cy', old_ext.get('cy'))


def page_art(book, part, cover_text=None, root=None):
    if root is None:
        root = ET.fromstring(book.read(part))
    rels = links(book, part)
    if cover_text is not None:
        for body in title_bodies(root):
            for paragraph in body.findall(A + 'p'):
                # Runs and line breaks may change inside the fixed title box.
                cover_text.append(''.join(t.text or '' for t in paragraph.iter(A + 't')))
                body.remove(paragraph)

    def visible(e):
        attrs = dict(e.attrib)
        if e.tag == P + 'cNvPr':
            attrs.pop('title', None)
            attrs.pop('descr', None)
        for key, value in list(attrs.items()):
            if key.startswith(R) and value in rels:
                kind, dest = rels[value]
                attrs[key] = hashlib.sha256(book.read(dest)).hexdigest() if kind == 'image' else kind
        return (e.tag, sorted(attrs.items()), (e.text or '').strip(), [visible(c) for c in e
                if c.tag not in (P + 'extLst', A + 'extLst', P + 'custDataLst')])

    if root.tag == A + 'theme':
        return visible(root.find(A + 'themeElements'))
    drawing = [visible(c) for c in root.find(P + 'cSld') if c.tag in (P + 'bg', P + 'spTree')]
    styles = [visible(c) for c in root if c.tag in (P + 'clrMap', P + 'clrMapOvr', P + 'txStyles')]
    return root.get('showMasterSp', '1'), drawing, styles


def signature(book, part, cover_text=None, root=None):
    layers = [page_art(book, part, cover_text, root)]
    for kind in ('slideLayout', 'slideMaster', 'theme'):
        part = next(dst for relation, dst in links(book, part).values() if relation == kind)
        layers.append(page_art(book, part))
    return layers


def presentation(book):
    part = 'ppt/presentation.xml'
    root = ET.fromstring(book.read(part))
    rels = links(book, part)
    pages = [rels[e.get(R + 'id')][1] for e in root.find(P + 'sldIdLst')]
    if len(pages) < 2:
        raise ValueError('缺少封面或封底')
    return pages, root.find(P + 'sldSz')


def inspect(filename, growth_template=None):
    with ZipFile(filename) as book:
        pages, size = presentation(book)
        cover = ET.fromstring(book.read(pages[0]))
        if growth_template is not None:
            with ZipFile(growth_template) as template:
                template_pages, template_size = presentation(template)
                reference = ET.fromstring(template.read(template_pages[0]))
                allow_downward_growth(reference, cover, int(template_size.get('cy')))
        texts = []
        assets = ((size.get('cx'), size.get('cy')), signature(book, pages[0], texts, cover),
                  signature(book, pages[-1]))
        return assets, ''.join(texts)


def compact(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def matches_cover(actual, title, presenter, date):
    """Reject missing/extra text, including old titles or dates left in another run."""
    heading = re.escape(compact(title))
    name = r'(?:汇报(?:人|单位|部门):?)?' + re.escape(compact(presenter))
    when = r'(?:(?:汇报)?(?:时间|日期):?)?' + re.escape(compact(date))
    sep = r'[|/·,、—–-]*'
    metadata = f'(?:{name}{sep}{when}|{when}{sep}{name})'
    return re.fullmatch(heading + sep + metadata, compact(actual)) is not None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('company_template')
    parser.add_argument('report')
    parser.add_argument('--title', help='完整封面题名，含需要保留的版本号')
    parser.add_argument('--presenter', '--reporter', help='已核实的汇报人或汇报单位')
    parser.add_argument('--date', help='已核实的汇报时间，按封面日期格式填写')
    parser.add_argument('--allow-cover-title-grow', action='store_true',
                        help='允许原封面题名框仅向下扩高至画布内，以整体下移署名和日期')
    args = parser.parse_args()
    fields = (args.title, args.presenter, args.date)
    check_fields = any(value is not None for value in fields)
    if check_fields and not all(value and compact(value) for value in fields):
        parser.error('--title、--presenter 和 --date 必须一起提供，且不能留空')
    if check_fields and any(re.fullmatch(r'待定|待补[充]?|待确认|待填写|[Xx×]+|汇报人|姓名|标题|日期',
                                         compact(value)) for value in fields):
        parser.error('请按本次任务提供已确认的封面字段，不能使用占位字。')

    expected, _ = inspect(args.company_template)
    actual, cover = inspect(args.report, args.company_template if args.allow_cover_title_grow else None)
    results = [x == y for x, y in zip(expected, actual)]
    for label, passed in zip(['画布', '固定封面', '固定封底'], results):
        print(label, '一致' if passed else '有差异')
    if check_fields:
        passed = matches_cover(cover, *fields)
        results.append(passed)
        print('封面题名、汇报人、汇报时间', '与指定值一致' if passed else '缺失、有多余文字或与指定值不符')
    else:
        print('封面业务字段未核对。成品请提供 --title、--presenter 和 --date。')
    print('本检查不涉及正文、文案质量和渲染效果。')
    raise SystemExit(0 if all(results) else 1)
