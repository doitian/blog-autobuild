#!/usr/bin/env python3

# pip install PyYAML Inflector

import os
import sys
import re
import json
import shutil
import subprocess
from dataclasses import dataclass
from yaml import load, dump
from urllib.parse import quote_plus

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
from inflector import English as EnglishInflector
from pathlib import Path

INFLECTOR = EnglishInflector()

SRC_DIR = Path(
    os.environ.get("KB_SRC_DIR", Path.home() / "Dropbox" / "Brain" / "output")
)
CONTENT_DIR = Path(
    os.environ.get("KB_CONTENT_DIR", Path.home() / "codebase" / "iany.me" / "content")
)
TEST_VECTORS = Path(os.path.realpath(__file__)).parent / "test-vectors"

YYMM_RE = re.compile(r"\d{4} - (.*)")
INLINE_MATH = re.compile(r"(^|[^\w$\\])(\$.*?[^\\]\$)(\W|$)")
EMBED_RE = re.compile(r'\[(\w+) - (.*)\]\((.*[^\s\'"])(?:\s+["\'](.*)["\'])?\)')
CONTENT_BLOCK_RE = re.compile(r"\s*!\[\[(.*)\]\]$")
ANCHOR_RE = re.compile(r"\^[a-zA-Z0-9][-a-zA-Z0-9]*$")
INLINE_ANCHOR_RE = re.compile(r"(?:\s)\^([a-zA-Z0-9][-a-zA-Z0-9]*)$")
CALLOUT_RE = re.compile(r"> \[!([^\]]*)\]([-+]?)(?: (.*))?")
INDENTATION_RE = re.compile(r"(\s*)")
LIST_PREFIX_RE = re.compile(r"(?:[0-9]+\.|[-*]) ")

IMAGE_EXTS = {".jpg": True, ".jpeg": True, ".png": True, ".gif": True, ".svg": True}

ARTICLES_INDEX = {}
BACKLINKS_COLLECTION = {}


def gostr(str):
    return repr(str).replace('"', '\\"')[1:-1]


class MachineIO:
    def __init__(self, parent, inputs, outputs, pc, converter_context=None):
        self.parent = parent
        self.inputs = inputs
        self.outputs = outputs
        self.pc = pc
        self.katex = False
        self.context = converter_context or {}

    def forward(self):
        if self.pc < len(self.inputs):
            line = self.inputs[self.pc]
            self.pc += 1
            return line

    def append(self, line):
        self.outputs.append(line)
        return self

    def squash_empty_lines(self):
        empty_line_already_exist = (
            len(self.outputs) == 0 or self.outputs[-1].rstrip("\r\n") == ""
        )
        while self.pc < len(self.inputs) and self.inputs[self.pc].rstrip("\r\n") == "":
            if not empty_line_already_exist:
                self.outputs.append(self.inputs[self.pc])
                empty_line_already_exist = True

            self.pc += 1

    def feed(self, lines):
        self.inputs = lines + self.inputs[self.pc :]
        self.pc = 0

    def read_file(self, file):
        with open(self.parent / file) as f:
            return f.read()

    def feed_file(self, file):
        self.feed(self.read_file(file).splitlines(keepends=True))

    def flush(self):
        return "".join(self.outputs)


@dataclass
class ContentBlock:
    path: str
    caption: str
    gallery_caption: str
    query: str
    kg_width: str
    end_row: bool

    def __init__(self, match):
        parts = match.group(1).split("|")
        self.path = parts[0]
        self.caption = ""
        self.gallery_caption = ""
        self.query = ""
        self.end_row = False
        self.kg_width = ""

        if self.path.startswith("./"):
            self.path = self.path[2:]

        if len(parts) > 1:
            self.caption = parts[1]

        if len(parts) > 2:
            self.query = parts[2]
            query_splits = self.query.split("?", 1)
            if query_splits[0].strip() in ["fit", "normal", "wide", "full"]:
                self.kg_width = query_splits[0].strip()
                self.query = query_splits[1].strip() if len(query_splits) > 1 else ""

        if len(parts) > 3:
            self.end_row = True
            self.gallery_caption = parts[3]


class StateIgnoredFencedCodeBlock:
    def parse(self, line, io):
        if line is None:
            fail("Unexpected EOF: open fenced code block")

        if line.strip() == "```":
            return StateNormal()
        else:
            return self


class StateFencedCodeBlock:
    def parse(self, line, io):
        if line is None:
            fail("Unexpected EOF: open fenced code block")

        io.append(line)

        if line.strip() == "```":
            return StateNormal()
        else:
            return self


class StateContentBlockImage:
    def __init__(self, matches):
        self.matches = matches

    def parse(self, line, io):
        cb_match = CONTENT_BLOCK_RE.match(line) if line is not None else None
        if cb_match:
            ext = os.path.splitext(cb_match.group(1).split("|")[0])[1]
            if ext in IMAGE_EXTS:
                self.matches.append(cb_match)
                return self

        if len(self.matches) == 1:
            cb = ContentBlock(self.matches[0])
            io.append("{{< image-card")
            src = cb.path
            if cb.query != "":
                src = src + "?" + cb.query
            io.append(" src=")
            io.append(strrepr(src))
            if cb.kg_width != "":
                io.append(" kg-width=")
                io.append(strrepr(cb.kg_width))
            if cb.caption != "":
                io.append(" caption=")
                io.append(strrepr(convert_line(cb.caption, io.katex, io.context)))

            io.append(" >}}")
        else:
            io.append("{{< gallery-card")
            blocks = [ContentBlock(m) for m in self.matches]
            cb_first = blocks[0]
            if cb_first.kg_width != "":
                io.append(" kg_width=")
                io.append(cb_first.kg_width)

            should_start_new_line = False
            for cb in blocks:
                if should_start_new_line:
                    io.append(' "|"')
                    should_start_new_line = False
                if cb.end_row:
                    should_start_new_line = True

                src = cb.path
                if cb.query != "":
                    src = src + "?" + cb.query
                if cb.caption != "":
                    src = src + "|" + cb.caption
                io.append(" ")
                io.append(strrepr(src))

            cb_last = blocks[-1]
            if cb_last.gallery_caption != "":
                io.append(" ")
                io.append(
                    strrepr(
                        "|"
                        + convert_line(cb_last.gallery_caption, io.katex, io.context)
                    )
                )

            io.append(" >}}")

        if line is not None:
            io.append("\n")

        return StateNormal().parse(line, io)


class StateMathBlock:
    def on_start(self, line, io):
        indentation, text = line.split("$$", 1)
        self.indentation = indentation
        io.append(indentation)
        io.append("``` katex\n")
        io.append(indentation)
        io.append("\\[")
        return self.parse(text, io)

    def parse(self, line, io):
        if line is None:
            fail("Unexpected EOF: open math block")
        if line.strip().endswith("$$"):
            io.append("\\]".join(line.rsplit("$$", 1)))
            if not line.endswith("\n"):
                io.append("\n")
            io.append(self.indentation)
            io.append("```\n")
            return StateNormal()
        else:
            io.append(line)
            return self


class StateComment:
    def on_start(self, line, io):
        if line.strip()[2:].endswith("%%"):
            io.squash_empty_lines()
            return StateNormal()
        else:
            return self

    def parse(self, line, io):
        if line.strip().endswith("%%"):
            io.squash_empty_lines()
            return StateNormal()
        else:
            return self


class StateMetadata:
    def on_start(self, line, io):
        return self

    def parse(self, line, io):
        if line is None:
            return

        if line.strip() == "":
            io.squash_empty_lines()
            return StateNormal()
        else:
            return self


CALLOUT_ICONS = {
    "example": "list",
    "code": "code",
    "file": "file",
    "info": "circle-info",
    "hint": "fire",
    "attention": "exclamation-triangle",
    "definition": "book",
}


class StateCallout:
    def on_start(self, match, io):
        self.state = StateNormal()

        kind, fold, title = match.groups()
        if title is None:
            title = kind.capitalize()
        icon = CALLOUT_ICONS[kind]

        io.outputs.append(
            f'{{{{< callout type="{kind}" icon="fas fa-{icon}" title="{gostr(title)}" fold="{fold}" >}}}}\n\n'
        )

        return self

    def parse(self, line, io):
        if line is not None and line.startswith("> "):
            self.state = self.state.parse(line[2:], io)
            return self
        elif line is not None and line.rstrip() == ">":
            self.state = self.state.parse(line[1:], io)
            return self
        else:
            io.outputs.append("\n{{< /callout >}}\n")
            return StateNormal().parse(line, io)


class StateNormal:
    def parse(self, line, io):
        if line is None:
            return

        if line.strip() == "":
            io.append(line)
            return self

        if line.strip() == "%%TOC%%":
            io.squash_empty_lines()
            return self

        if ANCHOR_RE.match(line.strip()):
            indentation, name = line.split("^", maxsplit=1)
            io.append('{}<a name="{}"></a>\n'.format(indentation, name.strip()))
            return self

        embed_match = EMBED_RE.match(line)
        if embed_match:
            convert_embed(line, embed_match, io)
            return self

        if line.strip().startswith("```dataviewx"):
            return StateIgnoredFencedCodeBlock()

        if line.strip().startswith("```"):
            io.append(line)
            return StateFencedCodeBlock()

        if io.katex and line.strip().startswith("$$"):
            return StateMathBlock().on_start(line, io)

        if line.strip().startswith("%%"):
            return StateComment().on_start(line, io)

        if line.strip().startswith("**") and "**:: " in line:
            return StateMetadata().on_start(line, io)

        callout_match = CALLOUT_RE.match(line)
        if callout_match:
            return StateCallout().on_start(callout_match, io)

        cb_match = CONTENT_BLOCK_RE.match(line)
        if cb_match:
            ext = os.path.splitext(cb_match.group(1).split("|")[0])[1]
            if ext in IMAGE_EXTS:
                return StateContentBlockImage([cb_match])
            else:
                filename = cb_match.group(1).split("|")[0]
                if filename.startswith("./"):
                    filename = filename[2:]
                if not filename.endswith(".md"):
                    filename = filename + ".md"
                io.feed_file(filename)
                return self

        io.append(convert_line(line, io.katex, io.context))
        return self


def get_publish_metadata(path):
    if "§ Blog" not in str(path):
        return None

    try:
        root_splits = path.parent.as_posix().split("/§ Blog/", 1)
        relative_root = Path(root_splits[1] if len(root_splits) > 1 else "")
        section = parse_section(relative_root)
        basename = parse_basename(relative_root)
        slug = slugify(basename)

        lang_ext = ""
        if path.name.endswith("- Chinese.md"):
            lang_ext = ".zh"

        # Returns relative path like: Section/Slug/index.zh.md
        # Using forward slashes for consistency in JSON keys
        return f"{section}/{slug}/index{lang_ext}.md"
    except Exception:
        return None


class Converter:
    def __init__(self, parent, front_matters, body, current_src_path):
        self.current_src_path = current_src_path
        self.context = {
            "src_path": current_src_path,
            "title": front_matters.get("title", ""),
            "publish_path": get_publish_metadata(current_src_path),
        }
        self.io = MachineIO(
            parent, body.strip().splitlines(keepends=True), [], 0, self.context
        )
        if "katex" in front_matters and front_matters["katex"]:
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
    return INFLECTOR.urlize(name.lower()).replace("_", "-")


def obsidian_link(name):
    # https://kb.iany.me/para/lets/f/Final+Cut+Pro/%E2%99%AF+Final+Cut+Pro
    basename = name[2:]
    tickler = basename[0].lower()
    if ord(tickler) < ord("a") or ord(tickler) > ord("z"):
        tickler = "_"

    return f"https://kb.iany.me/para/lets/{tickler}/{quote_plus(basename)}/{quote_plus(name)}"


def line_end(line):
    return line[len(line.rstrip()) :]


def parse_section(root):
    return INFLECTOR.urlize(INFLECTOR.singularize(root.parts[0]))


def find_article(basename):
    if basename in ARTICLES_INDEX:
        return ARTICLES_INDEX[basename]

    # collect
    for root, dirs, files in os.walk(SRC_DIR):
        root = Path(root)
        for file in files:
            file_path = root / file
            if should_publish(file_path):
                ARTICLES_INDEX[os.path.splitext(file)[0]] = file_path

    return ARTICLES_INDEX.get(basename)


def parse_basename(root):
    basename = root.name
    match = YYMM_RE.match(basename)
    if match:
        return match.group(1)
    return basename


# Obsidian style wikilink
WIKILINK = re.compile(r"\[\[([^ ].*?)\]\]")
RELATIVE_IMAGE = re.compile(
    r'!\[(.*?)\]\(\./([^)]*\.(?:jpe?g|png|svg))(?:\s+"(.*)")?\)'
)


def strrepr(str):
    if str is None:
        return ""
    return json.dumps(str, ensure_ascii=False)


def convert_embed(line, match, io):
    args = match.group(4) or ""
    if args != "":
        args = " " + args
    if match.group(1) == "Vimeo":
        id = match.group(3).split("vimeo.com/", 1)[-1]
        io.append(
            "{{{{< vimeo-card id={} caption={}{} >}}}}".format(
                strrepr(id), strrepr(match.group(2)), args
            )
        )
        io.append(line_end(line))
    else:
        io.append(line)


def convert_link(match, context):
    basename = match.group(1)
    title = basename
    if title.startswith("§ "):
        title = "♯ " + title[2:]

    if "|" in basename:
        basename, title = basename.split("|", 1)

    anchor = ""
    anchor_title = ""
    if "#^" in basename:
        basename, anchor = basename.split("#^", 1)
        anchor = "#" + anchor

    if "#" in basename:
        basename, anchor = basename.split("#", 1)
        anchor_title = anchor
        anchor = "#" + slugify(anchor)

    if basename == "":
        # link in current page
        if title.startswith("#^"):
            title = title[2:]
            if title.startswith("ref-"):
                title = title[4:]
            return '<sup id="fnxref:{}">[{}](#fnx:{})</sup> '.format(
                title, title, title
            )
        else:
            if title.startswith("#"):
                title = title[1:]
            return "[{}]({})".format(title, anchor)

    path = find_article(basename)
    if path is None:
        if basename.startswith("♯ "):
            return "[{}]({})".format(title, obsidian_link(basename))

        if basename == title:
            return "[{}]".format(basename)
        else:
            return "[{}][{}]".format(title, basename)

    if anchor == "" and "§" not in path.name:
        anchor = "#" + slugify(
            path.name[0:-12] if path.name.endswith("- Chinese.md") else path.name[0:-3]
        )

    lang = "en"
    if basename.endswith("- Chinese"):
        lang = "zh"

    if "§ Blog" not in path.parts:
        fail("Invalid link target: {}".format(path))

    relative_path = Path(*path.parts[path.parts.index("§ Blog") + 1 :])
    section = parse_section(relative_path)
    slug = slugify(parse_basename(relative_path.parent))

    # Record backlink if target is a publishable article
    target_publish_path = get_publish_metadata(path)

    if target_publish_path and context and context.get("publish_path"):
        if target_publish_path not in BACKLINKS_COLLECTION:
            BACKLINKS_COLLECTION[target_publish_path] = []

        # Check for duplicates
        backlink_entry = {
            "source_path": context["publish_path"],
            "source_title": context["title"],
            "target_anchor": anchor,
            "target_anchor_title": anchor_title,
        }

        exists = False
        for entry in BACKLINKS_COLLECTION[target_publish_path]:
            if (
                entry["source_path"] == backlink_entry["source_path"]
                and entry["target_anchor"] == backlink_entry["target_anchor"]
            ):
                exists = True
                break

        if not exists:
            BACKLINKS_COLLECTION[target_publish_path].append(backlink_entry)

    return '[{}]({{{{< relref path="/{}/{}.md" lang="{}" >}}}}{})'.format(
        title, section, slug, lang, anchor
    )


def convert_relative_img(match):
    alt = match.group(1)
    src = match.group(2)
    title = match.group(3) or ""
    return '{{{{< img src="{}" alt="{}" title="{}" >}}}}'.format(src, alt, title)


def convert_line(line, katex, context):
    line = WIKILINK.sub(lambda m: convert_link(m, context), line)
    line = RELATIVE_IMAGE.sub(convert_relative_img, line)

    inline_anchor_match = INLINE_ANCHOR_RE.search(line)
    if inline_anchor_match:
        refid = inline_anchor_match.group(1)
        if refid.startswith("ref-"):
            refid = refid[4:]
        line = line[: inline_anchor_match.start()] + f"&#160;[↩︎](#fnxref:{refid})"
        anchor = f'<a name="fnx:{refid}"></a> '
        _, indentation, stripped_line = INDENTATION_RE.split(line, maxsplit=1)
        list_prefix_match = LIST_PREFIX_RE.match(stripped_line)
        if list_prefix_match:
            list_prefix = list_prefix_match.group(0)
            line = "".join(
                [
                    indentation,
                    list_prefix,
                    anchor,
                    stripped_line[len(list_prefix) :].lstrip(),
                ]
            )
        else:
            line = "".join([indentation, anchor, stripped_line])

    if katex:
        line = INLINE_MATH.sub(r"\1`\2`\3", line)

    return line


def resolve_breadcrumbs(path, front_matters):
    ext = " - Chinese.md" if path.name.endswith(" - Chinese.md") else ".md"
    if "breadcrumbAncestors" not in front_matters:
        ancestors = []
        parent = path.parent.parent
        while (parent / "§ {}{}".format(parent.name, ext)).exists():
            ancestors.append("../{}/".format(slugify(parent.name)))
            parent = parent.parent
        if len(ancestors) > 0:
            ancestors.reverse()
            front_matters["breadcrumbAncestors"] = ancestors

    if "breadcrumbDescendants" not in front_matters:
        descendants = list(
            "../{}/".format(slugify(d.name))
            for d in sorted(path.parent.iterdir())
            if d.is_dir() and (d / "§ {}{}".format(d.name, ext)).exists()
        )
        if len(descendants) > 0:
            front_matters["breadcrumbDescendants"] = descendants


def convert_md(src):
    with open(src) as f:
        raw = f.read()

    printable_exceptions = "\n\r\t| "
    for i in range(len(raw)):
        c = raw[i]
        if not c.isprintable() and not c in printable_exceptions:
            context_start = i - 10 if i > 10 else 0
            fail(
                "File {} contains nonprintable char at {}: {}({})".format(
                    src, i, repr(c), repr(raw[context_start : context_start + 20])
                )
            )

    if raw.startswith("---\n"):
        content = raw.split("---\n", 2)
        if content[0] != "" and len(content) != 3:
            fail("Invalid file content: {}".format(src))

        front_matters = load(content[1], Loader=Loader) or {}

        body = content[2].strip()
    else:
        front_matters = {}
        body = raw

    if not body.startswith("# "):
        fail("{} does not have a title".format(src))

    body_splits = body.split("\n", 1)
    if len(body_splits) > 1:
        title_line, body = body.split("\n", 1)
        body = body.strip()
    else:
        title_line = body_splits[0]
        body = ""

    if "title" not in front_matters:
        front_matters["title"] = title_line[2:].strip()

    if "aliases" in front_matters:
        front_matters["obsidian_aliases"] = front_matters["aliases"]
        del front_matters["aliases"]
    if "hugo_aliases" in front_matters:
        front_matters["aliases"] = front_matters["hugo_aliases"]
        del front_matters["hugo_aliases"]

    if "banner" in front_matters:
        banner = front_matters["banner"]
        if banner.startswith("![["):
            front_matters["banner"] = banner[3:-2]

    if re.match(r"^#[a-zA-Z]", body):
        tags_splits = body.split("\n", 1)
        tags_splits = body.split("\n", 1)
        if len(tags_splits) > 1:
            tags_line, body = tags_splits
            body = body.strip()
        else:
            tags_line = tags_splits[0]
            body = ""
        front_matters["tags"] = tags_line.strip()[1:].split(" [[", 1)[0].split(" #")

    resolve_breadcrumbs(src, front_matters)
    descendants = []
    if "breadcrumbDescendants" in front_matters:
        descendants = front_matters["breadcrumbDescendants"]
        del front_matters["breadcrumbDescendants"]

    if "%%TOC%%" in body:
        front_matters["toc"] = True

    parts = ["---"]
    parts.append(
        dump(front_matters, Dumper=Dumper, width=999, allow_unicode=True).strip()
    )
    parts.append("---")
    parts.append("")
    parts.append(Converter(src.parent, front_matters, body, src).convert().rstrip())
    parts.append("")
    if len(descendants) > 0:
        if src.name.endswith(" - Chinese.md"):
            parts.append("## 索引\n")
        else:
            parts.append("## Index\n")
        for d in descendants:
            parts.append('* {{{{< rellink path="{}" >}}}}'.format(d))
        parts.append("")

    converted_body = "\n".join(parts)
    if "blog.iany.me/" in converted_body.replace("blog.iany.me/uploads", ""):
        if not front_matters.get("allowFullDomainLink", False):
            fail("File {} contains full domain link".format(src))

    return converted_body


def save_file(content, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="\n") as fd:
        fd.write(content)


def copy_file(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)


def copy_tree(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def should_publish(file):
    dir = str(file.parent)
    return file.exists() and file.name.startswith("§ ") and "§ Blog" in dir


def publish(root, versions, files, dirs):
    print("publish in {}".format(root))
    if "§ Blog" in str(root):
        root_splits = root.as_posix().split("/§ Blog/", 1)
        relative_root = Path(root_splits[1] if len(root_splits) > 1 else "")
        section = parse_section(relative_root)
        basename = parse_basename(relative_root)
    else:
        fail("Unknown root: {}".format(root))

    for v in versions:
        if v[2:] != basename + ".md" and v[2:] != basename + " - Chinese.md":
            fail("Invalid version file: {}".format(root / v))

    slug = slugify(basename)
    post_dir = CONTENT_DIR / section / slug

    for v in versions:
        content = convert_md(root / v)
        if content is None:
            continue

        if v.endswith("- Chinese.md"):
            dst = post_dir / "index.zh.md"
            default_lang_dst = post_dir / "index.md"
            if not default_lang_dst.exists():
                save_file("\n".join(["---", "headless: true", "---"]), default_lang_dst)

        else:
            dst = post_dir / "index.md"

        save_file(content, dst)

    for f in files:
        if Path(f).suffix in [".jpg", ".jpeg", ".png", ".gif", ".svg"]:
            copy_file(root / f, post_dir)
    for d in dirs:
        if d in ["assets", "images"]:
            copy_tree(root / d, post_dir)
        if d == "res":
            for f in (root / d).iterdir():
                if Path(f).suffix in [".jpg", ".jpeg", ".png", ".gif", ".svg"]:
                    copy_file(root / f, post_dir)


def is_watch_exec():
    for env in [
        "WATCHEXEC_CREATED_PATH",
        "WATCHEXEC_RENAMED_PATH",
        "WATCHEXEC_WRITTEN_PATH",
        "WATCHEXEC_REMOVED_PATH",
        "WATCHEXEC_META_CHANGED_PATH",
    ]:
        if env in os.environ:
            return True

    return False


def read_watch_exec_paths():
    return set(
        path
        for env in [
            "WATCHEXEC_CREATED_PATH",
            "WATCHEXEC_RENAMED_PATH",
            "WATCHEXEC_WRITTEN_PATH",
        ]
        for path in os.environ.get(env, "").split(":")
        if path != ""
    )


if __name__ == "__main__":
    comm = sys.argv[1] if len(sys.argv) > 1 else "test"

    if comm == "run":
        watch_changed_files = read_watch_exec_paths()
        watch_common_path = os.environ.get("WATCHEXEC_COMMON_PATH")

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
                    if changed_file.startswith("/"):
                        changed_file = Path(watch_common_path + changed_file).resolve()
                    else:
                        changed_file = (
                            Path(watch_common_path) / changed_file
                        ).resolve()
                else:
                    changed_file = Path(changed_file).resolve()

                dirs = [p.name for p in changed_file.parent.iterdir() if p.is_dir()]
                if should_publish(changed_file):
                    publish(changed_file.parent, [changed_file.name], [], dirs)
                elif changed_file.name.endswith(".md"):
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

        # --- Collect and save backlinks ---
        print("Collecting and saving backlinks...")
        backlinks_output_dir = CONTENT_DIR.parent / "data"
        backlinks_output_dir.mkdir(parents=True, exist_ok=True)
        backlinks_output_file = backlinks_output_dir / "backlinks.json"

        with open(backlinks_output_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(BACKLINKS_COLLECTION, f, indent=2, ensure_ascii=False)
        print(f"Backlinks saved to {backlinks_output_file}")

        exit(0)

    if len(sys.argv) <= 2:
        for root, dirs, files in os.walk(TEST_VECTORS):
            root = Path(root)
            for file in files:
                if file.endswith(".in.md"):
                    out_file = file[:-6] + ".out.md"
                    real_file = file[:-6] + ".real.md"

                    output = convert_md(root / file)
                    with open(root / real_file, "w") as fout:
                        fout.write(output)

                    if open(root / out_file).read() != output:
                        subprocess.run(["diff", root / out_file, root / real_file])
                        fail("test fail: {}".format(file))
                    else:
                        print("test pass: {}".format(file))

        exit(0)
