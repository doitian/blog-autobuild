#!/usr/bin/env python3

import os
import re
from pathlib import Path
from urllib.parse import quote

from pyvis.network import Network

LINKED_REF_TITLE_RE = re.compile('^## .* linked references? to')
CROSS_LINK_RE = re.compile('ia-writer://.*?\.md')
CONTENT_BLOCK_MD_RE = re.compile(r'\s*(/(?:.+/)*.+\.md)(\s.*|$)')


def should_publish(file):
    dir = str(file.parent)
    return file.exists() and file.name.startswith('♯ ') and ('§ Tickler' in dir)


def path_to_link(file):
    return "ia-writer://open?path=/Locations/iCloud/§" + str(file).split('§', 1)[1].replace(' ', '%20')


def tag_to_link(tag):
    return 'ia-writer://quick-search?query=%23{}'.format(quote(tag))


def resolve_breadcrumbs(path, front_matters):
    ext = ' - Chinese.md' if path.name.endswith(' - Chinese.md') else '.md'
    ancestors = []
    parent = path.parent.parent
    parent_article = parent / "♯ {}{}".format(parent.name, ext)
    while parent_article.exists():
        ancestors.append(path_to_link(parent_article))
        parent = parent.parent
        parent_article = parent / "♯ {}{}".format(parent.name, ext)
    ancestors.reverse()
    front_matters['breadcrumbAncestors'] = ancestors


class Entry():
    def __init__(self):
        self.title = ''
        self.link = ''
        self.cross_links = []
        self.tags = []
        self.ancestors = []

    def parse(self, file):
        self.link = path_to_link(file)

        with open(file) as f:
            raw = f.read()

        if raw.startswith('---\n'):
            content = raw.split('---\n', 2)
            if content[0] != '' and len(content) != 3:
                fail("Invalid file content: {}".format(file))

            front_matters = load(content[1], Loader=Loader) or {}

            body = content[2].strip()
        else:
            front_matters = {}
            body = raw

        if not body.startswith('# '):
            fail("{} does not have a title".format(file))

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

        resolve_breadcrumbs(file, front_matters)

        self.title = front_matters['title']
        self.tags = front_matters.get('tags', [])
        self.ancestors = front_matters['breadcrumbAncestors']

        body = LINKED_REF_TITLE_RE.split(body)[0]

        def read_content_block(m):
            with open(file.parent / m.group(1)[1:]) as f:
                return f.read()

        body = CONTENT_BLOCK_MD_RE.sub(read_content_block, body)
        for m in CROSS_LINK_RE.finditer(body):
            self.cross_links.append(m.group(0))


class KnowledgeBase():
    def __init__(self):
        self.tags = {}
        self.network = Network(width='100%', height='100%')
        self.network.toggle_physics(True)
        self.pending_edges = []

    def add_versions(self, root, versions):
        for v in versions:
            file = root / v
            entry = Entry()
            entry.parse(file)
            self.network.add_node(entry.link, label=entry.title,
                                  title='<a href="{}">Open in iA Writer</a>'.format(entry.link), shape='ellipse', level=len(entry.ancestors) + 1, physics=True)

            for link in entry.cross_links:
                if '§ Tickler' in link:
                    self.pending_edges.append({
                        'source': entry.link,
                        'to': link,
                        'width': 2,
                        'color': 'green'
                    })
            for link in entry.ancestors:
                self.pending_edges.append({
                    'source': link,
                    'to': entry.link,
                    'physics': True
                })
            for tag in entry.tags:
                if tag not in self.tags:
                    tag_link = tag_to_link(tag)
                    self.tags[tag] = tag_link
                    self.network.add_node(
                        tag_link, label='#' + tag, title='<a href="{}">Open in iA Writer</a>'.format(tag_link), shape='box', color='#eeeeee', level=0)
                else:
                    tag_link = self.tags[tag]
                self.network.add_edge(tag_link, entry.link)

        for v1 in versions:
            link1 = path_to_link(root / v1)
            for v2 in versions:
                if v1 != v2:
                    link2 = path_to_link(root / v2)
                    self.network.add_edge(link1, link2)

    def submit_edges(self):
        for edge in self.pending_edges:
            self.network.add_edge(**edge)

    def save(self, name):
        self.network.show(name)


if __name__ == '__main__':
    kb = KnowledgeBase()
    SRC_DIR = Path(os.environ.get('KB_SRC_DIR', Path.home() /
                                  "codebase" / "my" / "knowledge-base" / "§ Tickler"))

    for root, dirs, files in os.walk(SRC_DIR):
        root = Path(root)
        versions = []
        for file in files:
            if should_publish(root / file):
                versions.append(file)

        if len(versions) > 0:
            kb.add_versions(root, versions)

    kb.submit_edges()
    kb.network.set_options("""{
        "interaction": {
            "keyboard": {
            "enabled": true
            }
        }
    }""")
    if os.path.exists('../iany.me'):
        os.makedirs('../iany.me/static/wiki/net/', exist_ok=True)
        kb.save('../iany.me/static/wiki/net/index.html')
    else:
        kb.save('kbnet.html')
