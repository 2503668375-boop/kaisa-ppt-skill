"""Compare a native PPTX with preselected reference slides. Requires Python 3."""
import argparse
import hashlib
import json
import posixpath
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
OBJECTS = {P + key for key in ('sp', 'pic', 'graphicFrame', 'grpSp', 'cxnSp')}


def relationships(package, part):
    folder, filename = posixpath.split(part)
    rels = posixpath.join(folder, '_rels', filename + '.rels')
    return {item.get('Id'): (item.get('Type').rsplit('/', 1)[-1],
            posixpath.normpath(posixpath.join(folder, item.get('Target'))).lstrip('/'))
            for item in ET.fromstring(package.read(rels)) if item.get('TargetMode') != 'External'}


def presentation(package):
    part = 'ppt/presentation.xml'
    root = ET.fromstring(package.read(part))
    rels = relationships(package, part)
    return (root.find(P + 'sldSz').attrib,
            [rels[item.get(R + 'id')][1] for item in root.find(P + 'sldIdLst')])


def signature(element, palette):
    if element is None:
        return None
    attrs = dict(element.attrib)
    if element.tag == A + 'srgbClr':
        attrs['val'] = palette.get(attrs['val'], attrs['val'])
    return element.tag, sorted(attrs.items()), [signature(child, palette) for child in element]


def objects(package, part, slots, palette):
    root = ET.fromstring(package.read(part))
    rels = relationships(package, part)
    result = []
    used = set()

    def visit(container):
        for shape in container:
            if shape.tag not in OBJECTS:
                continue
            obj_id = shape.find('.//' + P + 'cNvPr').get('id')
            slot = slots.get(obj_id)
            data = [obj_id, shape.tag]
            for name in ('spPr', 'style', 'xfrm', 'grpSpPr'):
                prop = shape.find(P + name)
                if prop is not None and slot == 'diagram' and name == 'grpSpPr':
                    # Preserve the outer map/diagram frame; its local drawing coordinates can change.
                    prop = ET.fromstring(ET.tostring(prop))
                    for transform in (A + 'chOff', A + 'chExt'):
                        child = prop.find('.//' + transform)
                        if child is not None:
                            prop.find(A + 'xfrm').remove(child)
                data.append(signature(prop, palette))
            data.append(signature(shape.find(P + 'txBody/' + A + 'bodyPr'), palette))
            grid = shape.find('.//' + A + 'tblGrid') if shape.tag == P + 'graphicFrame' else None
            data.append(signature(grid, palette))
            if grid is not None:
                data.append([row.get('h') for row in shape.findall('.//' + A + 'tr')])
            text = shape.find(P + 'txBody/' + A + 'p')
            if text is not None:
                data.append(signature(text.find(A + 'pPr'), palette))
                run = text.find(A + 'r/' + A + 'rPr')
                if run is not None:
                    for link in list(run):
                        if link.tag in (A + 'hlinkClick', A + 'hlinkMouseOver'):
                            run.remove(link)
                data.append(signature(run, palette))
            if shape.tag == P + 'pic':
                if slot == 'picture':
                    used.add(obj_id)
                else:
                    fill = shape.find(P + 'blipFill')
                    blip = fill.find(A + 'blip')
                    data.append(hashlib.sha256(package.read(rels[blip.get(R + 'embed')][1])).hexdigest())
                    data.append(signature(fill.find(A + 'srcRect'), palette))
            result.append(data)
            if shape.tag == P + 'grpSp':
                if slot == 'diagram':
                    used.add(obj_id)
                else:
                    visit(shape)

    visit(root.find(P + 'cSld/' + P + 'spTree'))
    if set(slots) != used:
        raise ValueError('declared replacement is missing or has the wrong object type')
    return result


def ancestor(package, part, kind):
    return next(path for rel, path in relationships(package, part).values() if rel == kind)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('map', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.map.read_text(encoding='utf-8'))
    if hashlib.sha256(args.reference.read_bytes()).hexdigest() != plan['reference_sha256']:
        sys.exit('原稿与制版前的记录不一致')
    count = 0
    with ZipFile(args.reference) as ref, ZipFile(args.output) as out:
        rs, rp = presentation(ref)
        os, op = presentation(out)
        if (rs.get('cx'), rs.get('cy')) != (os.get('cx'), os.get('cy')) or len(op) != len(plan['pages']):
            sys.exit('画布尺寸或页数与记录不一致')
        for i, (mapping, new_part) in enumerate(zip(plan['pages'], op), 1):
            ref_page = mapping['source']
            if not 1 <= ref_page <= len(rp):
                sys.exit('原稿页码无效')
            old_part = rp[ref_page - 1]
            slots = mapping.get('slots', {})
            if i == 1 and ref_page == 1 and slots:
                sys.exit('封面品牌图和底图不能作为可替换业务图槽')
            palette = plan.get('accent_replacement', {})
            try:
                ok = objects(ref, old_part, slots, palette) == objects(out, new_part, slots, {})
                for kind in ('slideLayout', 'slideMaster'):
                    old_part = ancestor(ref, old_part, kind)
                    new_part = ancestor(out, new_part, kind)
                    ok = ok and objects(ref, old_part, {}, {}) == objects(out, new_part, {}, {})
            except (ValueError, KeyError, StopIteration) as exc:
                ok = False
                print(f'P{i}: {exc}')
            count += ok
            print(f'成品 P{i} / 原稿 P{ref_page}：' + ('一致' if ok else '不一致'))
    print(f'{count}/{len(op)} 页结构匹配；仍须并排查看实际渲染。')
    return 0 if count == len(op) else 1


if __name__ == '__main__':
    sys.exit(main())
