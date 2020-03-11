#!/usr/bin/env python3

import os
import sys
import re
import json
import urllib
import shutil
import subprocess
from dataclasses import dataclass
from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
from inflector import English as EnglishInflector
from pathlib import Path

INFLECTOR = EnglishInflector()

SRC_DIR = Path.home() / "codebase" / "my" / "ia-writer"
CONTENT_DIR = Path.home() / "codebase" / "my" / "iany.me" / "content"
TEST_VECTORS = Path(os.path.realpath(__file__)).parent / "test-vectors"

TICKLER_RE = re.compile(r'^tickler-')
YYMM_RE = re.compile(r'\d{4} - (.*)')
INLINE_MATH = re.compile(r'(^|[^\w$\\])(\$.*?[^\\]\$)(\W|$)')
EMBED_RE = re.compile(
    r'\[(\w+) - (.*)\]\((.*[^\s\'"])(?:\s+["\'](.*)["\'])?\)')
CONTENT_BLOCK_IMAGE = re.compile(r'\s*(/(?:.+/)*.+\.(?:png|jpg))(\s.*|$)')
CONTENT_BLOCK_MD = re.compile(r'\s*(/(?:.+/)*.+\.md)(\s.*|$)')
CONTENT_BLOCK_CSV = re.compile(r'\s*(/(?:.+/)*.+\.csv)(\s.*|$)')
CONTENT_BLOCK_OTHER = re.compile(r'\s*(/(?:.+/)*.+\.[-_\w]+)(\s.*|$)')


class MachineIO():
    def __init__(self, parent, inputs, outputs, pc):
        self.parent = parent
        self.inputs = inputs
        self.outputs = outputs
        self.pc = pc
        self.katex = False

    def forward(self):
        if self.pc < len(self.inputs):
            line = self.inputs[self.pc]
            self.pc += 1
            return line

    def append(self, line):
        self.outputs.append(line)
        return self

    def feed(self, lines):
        self.inputs = lines + self.inputs[self.pc:]
        self.pc = 0

    def read_file(self, file):
        with open(self.parent / file) as f:
            return f.read()

    def feed_file(self, file):
        self.feed(self.read_file(file).splitlines(keepends=True))

    def flush(self):
        return "".join(self.outputs)


@dataclass
class ContentBlock():
    path: str
    caption: str
    gallery_caption: str
    query: str
    kg_width: str
    end_row: bool

    def __init__(self, match):
        self.path = match.group(1)
        self.caption = ''
        self.gallery_caption = ''
        self.query = ''
        self.end_row = False
        self.kg_width = ''

        remaining = match.group(2).strip()
        if remaining == '':
            return

        if remaining.startswith('"'):
            splits = remaining[1:].split('"', 1)
            self.caption = splits[0].strip()
            remaining = splits[1].strip()
        elif remaining.startswith('('):
            splits = remaining[1:].split(')', 1)
            self.caption = splits[0].strip()
            remaining = splits[1].strip()

        if remaining == '|':
            self.end_row = True
            return

        splits = remaining.split('|', 1)
        self.query = splits[0].strip()
        query_splits = self.query.split('?', 1)
        if query_splits[0].strip() in ['fit', 'normal', 'wide', 'full']:
            self.kg_width = query_splits[0].strip()
            self.query = query_splits[1].strip() if len(
                query_splits) > 1 else ''
        if len(splits) > 1:
            self.end_row = True
            self.gallery_caption = splits[1].strip()


class StateFencedCodeBlock():
    def parse(self, line, io):
        if line is None:
            fail("Unexpected EOF: open fenced code block")

        io.append(line)

        if line.strip() == '```':
            return StateNormal()
        else:
            return self


class StateContentBlockImage():
    def __init__(self, matches):
        self.matches = matches

    def parse(self, line, io):
        cb_image = CONTENT_BLOCK_IMAGE.match(
            line) if line is not None else None
        if cb_image:
            self.matches.append(cb_image)
            return self

        if len(self.matches) == 1:
            cb = ContentBlock(self.matches[0])
            io.append("{{< image-card")
            src = cb.path[1:]
            if cb.query != '':
                src = src + '?' + cb.query
            io.append(' src=')
            io.append(strrepr(src))
            if cb.kg_width != '':
                io.append(' kg-width=')
                io.append(strrepr(cb.kg_width))
            if cb.caption != '':
                io.append(' caption=')
                io.append(strrepr(convert_line(cb.caption, io.katex)))

            io.append(" >}}")
        else:
            io.append("{{< gallery-card")
            cb_first = ContentBlock(self.matches[0])
            if cb_first.kg_width != '':
                io.append(' kg_width=')
                io.append(cb_first.kg_width)

            should_start_new_line = False
            for cb in (ContentBlock(m) for m in self.matches):
                if should_start_new_line:
                    io.append(' "|"')
                    should_start_new_line = False
                if cb.end_row:
                    should_start_new_line = True

                src = cb.path[1:]
                if cb.query != '':
                    src = src + '?' + cb.query
                if cb.caption != '':
                    src = src + '|' + cb.caption
                io.append(' ')
                io.append(strrepr(src))

            cb_last = ContentBlock(self.matches[-1])
            if cb_last.gallery_caption != '':
                io.append(' ')
                io.append(
                    strrepr('|' + convert_line(cb_last.gallery_caption, io.katex)))

            io.append(" >}}")

        if line is not None:
            io.append("\n")

        return StateNormal().parse(line, io)


class StateMathBlock():
    def on_start(self, line, io):
        indentation, text = line.split('\\\\[')
        self.indentation = indentation
        io.append(indentation)
        io.append('``` katex\n')
        io.append(indentation)
        io.append('\\[')
        return self.parse(text, io)

    def parse(self, line, io):
        if line is None:
            fail("Unexpected EOF: open math block")
        if line.strip().endswith('\\\\]'):
            io.append('\\]'.join(line.split('\\\\]')))
            if not line.endswith('\n'):
                io.append('\n')
            io.append(self.indentation)
            io.append('```\n')
            return StateNormal()
        else:
            io.append(line)
            return self


class StateNormal():
    def parse(self, line, io):
        if line is None:
            return

        if line.strip() == '':
            io.append(line)
            return self

        embed_match = EMBED_RE.match(line)
        if embed_match:
            convert_embed(line, embed_match, io)
            return self

        if line.strip().startswith('```'):
            io.append(line)
            return StateFencedCodeBlock()

        if io.katex and line.strip().startswith('\\\\['):
            return StateMathBlock().on_start(line, io)

        cb_image = CONTENT_BLOCK_IMAGE.match(line)
        if cb_image:
            return StateContentBlockImage([cb_image])

        cb_md = CONTENT_BLOCK_MD.match(line)
        if cb_md:
            io.feed_file(cb_md.group(1)[1:])
            return self

        io.append(convert_line(line, io.katex))
        return self


class Converter():
    def __init__(self, parent, front_matters, body):
        self.io = MachineIO(
            parent, body.strip().splitlines(keepends=True), [], 0)
        if 'katex' in front_matters and front_matters['katex']:
            self.io.katex = True
        self.state = StateNormal()

    def convert(self):
        while self.state:
            line = self.io.forward()
            self.state = self.state.parse(line, self.io)

        return self.io.flush()


def fail(reason):
    print(reason)
    exit(1)


def slugify(name):
    return INFLECTOR.urlize(name.lower()).replace('_', '-')


def line_end(line):
    return line[len(line.rstrip()):]


def parse_section(root):
    return INFLECTOR.urlize(INFLECTOR.singularize(root.parts[0]))


def parse_basename(root):
    basename = root.name
    match = YYMM_RE.match(basename)
    if match:
        return match.group(1)
    return basename


# ia-writer://open?path=/Locations/iCloud/§%20Blog/Posts/Posts%202017/201710%20-%20Lua%20C%20Api%20Userdata/♯%20Lua%20C%20Api%20Userdata%20-%20Chinese.md
IA_WRITER_LINK = re.compile(r'ia-writer://.*?\.md')
RELATIVE_IMAGE = re.compile(
    r'!\[(.*?)\]\(\./([^)]*\.(?:jpg|png))(?:\s+"(.*)")?\)')


def strrepr(str):
    if str is None:
        return ''
    return json.dumps(str, ensure_ascii=False)


def convert_embed(line, match, io):
    args = match.group(4) or ''
    if args != '':
        args = ' ' + args
    if match.group(1) == 'Vimeo':
        id = match.group(3).split('vimeo.com/', 1)[-1]
        io.append(
            "{{{{< vimeo-card id={} caption={}{} >}}}}".format(strrepr(id), strrepr(match.group(2)), args))
        io.append(line_end(line))
    else:
        io.append(line)


def convert_link(match):
    if '§%20Blog/' in match.group(0):
        path = Path(urllib.parse.unquote(
            match.group(0).split('§%20Blog/', 1)[1]))
        section = parse_section(path)
        slug = slugify(parse_basename(path.parent))
        lang = 'en'
        if path.name.endswith('- Chinese.md'):
            lang = 'zh'

        return '{{{{< relref path="/{}/{}.md" lang="{}" >}}}}'.format(section, slug, lang)

    path = Path(urllib.parse.unquote(
        match.group(0).split('§%20Tickler/', 1)[1]))
    section = 'wiki'
    slug = slugify(parse_basename(path.parent))
    lang = 'en'
    if path.name.endswith('- Chinese.md'):
        lang = 'zh'

    return '{{{{< relref path="/{}/{}.md" lang="{}" >}}}}'.format(section, slug, lang)


def convert_relative_img(match):
    alt = match.group(1)
    src = match.group(2)
    title = match.group(3) or ''
    return '{{{{< img src="{}" alt="{}" title="{}" >}}}}'.format(src, alt, title)


def convert_line(line, katex):
    line = IA_WRITER_LINK.sub(convert_link, line)
    line = RELATIVE_IMAGE.sub(convert_relative_img, line)

    if katex:
        line = INLINE_MATH.sub('\g<1>`\g<2>`\g<3>', line)

    return line


def resolve_breadcrumbs(path, front_matters):
    ext = ' - Chinese.md' if path.name.endswith(' - Chinese.md') else '.md'
    if 'breadcrumbAncestors' not in front_matters:
        ancestors = []
        parent = path.parent.parent
        while (parent / "♯ {}{}".format(parent.name, ext)).exists():
            ancestors.append("../{}/".format(slugify(parent.name)))
            parent = parent.parent
        if len(ancestors) > 0:
            ancestors.reverse()
            front_matters['breadcrumbAncestors'] = ancestors

    if 'breadcrumbDescendants' not in front_matters:
        descendants = list(
            "../{}/".format(slugify(d.name))
            for d in path.parent.iterdir()
            if d.is_dir() and (d / "♯ {}{}".format(d.name, ext)).exists()
        )
        if len(descendants) > 0:
            front_matters['breadcrumbDescendants'] = descendants


def convert_md(src):
    with open(src) as f:
        raw = f.read()

    if raw.startswith('---\n'):
        content = open(src).read().split('---\n', 2)
        if content[0] != '' and len(content) != 3:
            fail("Invalid file content: {}".format(src))

        front_matters = load(content[1], Loader=Loader) or {}

        body = content[2].strip()
    else:
        front_matters = {}
        body = raw

    if not body.startswith('# '):
        fail("{} does not have a title".format(src))

    body_splits = body.split('\n', 1)
    if len(body_splits) > 1:
        title_line, body = body.split('\n', 1)
        body = body.strip()
    else:
        title_line = body_splits[0]
        body = ''

    if 'title' not in front_matters:
        front_matters['title'] = title_line[2:].strip()

    if re.match(r'^#[a-zA-Z]', body):
        tags_splits = body.split('\n', 1)
        if len(tags_splits) > 1:
            tags_line, body = tags_splits
            body = body.strip()
        else:
            tags_line = tags_splits[0]
            body = ''
        front_matters['tags'] = tags_line.strip()[1:].split(' #')

    resolve_breadcrumbs(src, front_matters)
    descendants = []
    if 'breadcrumbDescendants' in front_matters:
        descendants = front_matters['breadcrumbDescendants']
        del front_matters['breadcrumbDescendants']

    parts = ['---']
    parts.append(dump(front_matters, Dumper=Dumper,
                      width=999, allow_unicode=True).strip())
    parts.append('---')
    parts.append('')
    parts.append(Converter(src.parent, front_matters, body).convert().rstrip())
    parts.append('')
    if len(descendants) > 0:
        if src.name.endswith(' - Chinese.md'):
            parts.append('## 索引\n')
        else:
            parts.append('## Index\n')
        for d in descendants:
            parts.append('* {{{{< rellink path="{}" >}}}}'.format(d))
        parts.append('')

    return "\n".join(parts)


def save_file(content, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, 'w') as fd:
        fd.write(content)


def copy_file(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)


def copy_tree(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def should_publish(file):
    dir = str(file.parent)
    return file.exists() and file.name.startswith('♯ ') and ('§ Tickler' in dir or '§ Blog' in dir)


def publish(root, versions, files, dirs):
    print("publish in {}".format(root))
    if '§ Tickler' in str(root):
        root_splits = str(root).split('/§ Tickler/', 1)
        relative_root = Path(root_splits[1] if len(root_splits) > 1 else '')
        section = 'wiki'
        basename = parse_basename(relative_root)
    elif '§ Blog' in str(root):
        root_splits = str(root).split('/§ Blog/', 1)
        relative_root = Path(root_splits[1] if len(root_splits) > 1 else '')
        section = parse_section(relative_root)
        basename = parse_basename(relative_root)
    else:
        fail("Unknown root: {}".format(root))

    for v in versions:
        if v[2:] != basename + '.md' and v[2:] != basename + ' - Chinese.md':
            fail('Invalid version file: {}'.format(root / v))

    slug = slugify(basename)
    post_dir = CONTENT_DIR / section / slug

    for v in versions:
        content = convert_md(root / v)
        if content is None:
            continue

        if v.endswith('- Chinese.md'):
            dst = post_dir / 'index.zh.md'
        else:
            dst = post_dir / 'index.md'

        save_file(content, dst)

    for f in files:
        if Path(f).suffix in ['.jpg', '.png', '.gif']:
            copy_file(root / f, post_dir)
    for d in dirs:
        if d in ['assets', 'images']:
            copy_tree(root / d, post_dir)


def is_watch_exec():
    for env in ['WATCHEXEC_CREATED_PATH', 'WATCHEXEC_RENAMED_PATH', 'WATCHEXEC_WRITTEN_PATH', 'WATCHEXEC_REMOVED_PATH', 'WATCHEXEC_META_CHANGED_PATH']:
        if env in os.environ:
            return True

    return False


def read_watch_exec_paths():
    return set(
        path
        for env in ['WATCHEXEC_CREATED_PATH', 'WATCHEXEC_RENAMED_PATH', 'WATCHEXEC_WRITTEN_PATH']
        for path in os.environ.get(env, '').split(':') if path != ''
    )


if __name__ == '__main__':
    comm = sys.argv[1] if len(sys.argv) > 1 else 'test'

    if comm == 'run':
        watch_changed_files = read_watch_exec_paths()
        watch_common_path = os.environ.get('WATCHEXEC_COMMON_PATH')

        if is_watch_exec():
            if len(watch_changed_files) > 0:
                print("\n/***********************************")
                if watch_common_path:
                    print("* {}/**".format(watch_common_path))
                for f in watch_changed_files:
                    print("*   {}".format(f))
                print(" **********************************/")

            for changed_file in watch_changed_files:
                if watch_common_path:
                    if changed_file.startswith('/'):
                        changed_file = Path(
                            watch_common_path + changed_file).resolve()
                    else:
                        changed_file = (
                            Path(watch_common_path) / changed_file).resolve()
                else:
                    changed_file = Path(changed_file).resolve()

                dirs = [p.name for p in changed_file.parent.iterdir()
                        if p.is_dir()]
                if should_publish(changed_file):
                    publish(changed_file.parent, [changed_file.name], [], dirs)
                elif changed_file.name.endswith('.md'):
                    for p in changed_file.parent.iterdir():
                        if should_publish(p):
                            publish(p.parent, [p.name], [], dirs)

            exit(0)

        for root, dirs, files in os.walk(SRC_DIR):
            root = Path(root)
            versions = []
            for file in files:
                if should_publish(root / file):
                    versions.append(file)

            if len(versions) > 0:
                publish(root, versions, files, dirs)

        exit(0)

    if len(sys.argv) <= 2:
        for root, dirs, files in os.walk(TEST_VECTORS):
            root = Path(root)
            for file in files:
                if file.endswith('.in.md'):
                    out_file = file[:-6] + '.out.md'
                    real_file = file[:-6] + '.real.md'

                    output = convert_md(root / file)
                    with open(root / real_file, 'w') as fout:
                        fout.write(output)

                    if open(root / out_file).read() != output:
                        subprocess.run(
                            ['diff', root / out_file, root / real_file])
                        fail("test fail: {}".format(file))
                    else:
                        print("test pass: {}".format(file))

        exit(0)

    for arg in sys.argv[2:]:
        print(convert_md(Path(arg)))
