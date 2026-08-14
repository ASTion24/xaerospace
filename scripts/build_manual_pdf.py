from __future__ import annotations

import argparse
import base64
import html
import re
import runpy
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
import markdown
from bs4 import BeautifulSoup
from fontTools.ttLib import TTCollection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = runpy.run_path(
    str(PROJECT_ROOT / "src" / "aerospace_simulator" / "_version.py")
)["__version__"]
DEFAULT_SOURCE = PROJECT_ROOT / "docs" / f"v{VERSION}_product_demo_zh.md"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / f"Xaerospace_v{VERSION}_使用手册.pdf"
SYSTEM_CJK = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
SYSTEM_LATIN = Path("/System/Library/Fonts/Avenir Next.ttc")
SYSTEM_MONO = Path("/System/Library/Fonts/Menlo.ttc")
CHROME_CANDIDATES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)

PARTS = (
    (1, "ORIENTATION", "系统与能力", range(1, 3), "#1f5eff"),
    (2, "CONFIGURATION", "Provider 与启动", range(3, 9), "#00a190"),
    (3, "OPERATIONS", "真实任务与结果", range(9, 17), "#f04438"),
    (4, "REFERENCE", "接口、安全与故障", range(17, 23), "#171d20"),
)
CHAPTER_PAGE_BREAKS = {
    2,
    4,
    10,
    11,
    12,
    16,
    19,
}
CHAPTER_LABELS = {
    1: "SYSTEM OVERVIEW",
    2: "CAPABILITY MAP",
    3: "PROVIDER SYSTEM",
    4: "INSTALLATION",
    5: "LOCAL PROVIDER",
    6: "CLOUD API",
    7: "CUSTOM GATEWAY",
    8: "PROFILE SWITCHING",
    9: "LAUNCH TO ORBIT",
    10: "ROCKET FLIGHT",
    11: "MULTI-DOMAIN RUNS",
    12: "MODEL EQUATIONS",
    13: "WORKFLOW REPLAY",
    14: "STATE HANDOVER",
    15: "ASSISTANT SESSIONS",
    16: "RESULT ARTIFACTS",
    17: "HTTP API",
    18: "CLI",
    19: "PROVIDER SAFETY",
    20: "PHYSICS SAFETY",
    21: "KNOWN LIMITS",
    22: "TROUBLESHOOTING",
}


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str

    @property
    def anchor(self) -> str:
        return f"chapter-{self.number:02d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Xaerospace Chinese user manual from Markdown."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chrome", type=Path)
    parser.add_argument(
        "--keep-html",
        type=Path,
        help="Optionally retain the final intermediate HTML at this path.",
    )
    return parser.parse_args()


def data_uri(path: Path, mime: str = "font/ttf") -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def locate_chrome(explicit: Path | None) -> Path:
    if explicit is not None:
        if explicit.is_file():
            return explicit
        raise RuntimeError(f"Chrome executable does not exist: {explicit}")
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)
    raise RuntimeError("Chrome or Chromium is required to build the manual")


def prepare_cjk_fonts(directory: Path) -> tuple[Path, Path]:
    if not SYSTEM_CJK.is_file():
        raise RuntimeError(
            "Hiragino Sans GB is required for deterministic Chinese rendering"
        )
    regular = directory / "xa-cjk-regular.ttf"
    bold = directory / "xa-cjk-bold.ttf"
    collection = TTCollection(str(SYSTEM_CJK))
    collection.fonts[0].save(regular)
    collection.fonts[2].save(bold)
    return regular, bold


def prepare_latin_fonts(directory: Path) -> tuple[Path, Path, Path]:
    missing = [path for path in (SYSTEM_LATIN, SYSTEM_MONO) if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Required macOS system fonts are missing: "
            + ", ".join(str(path) for path in missing)
        )
    regular = directory / "xa-latin-regular.ttf"
    bold = directory / "xa-latin-bold.ttf"
    mono = directory / "xa-mono-regular.ttf"
    latin_collection = TTCollection(str(SYSTEM_LATIN))
    latin_collection.fonts[7].save(regular)
    latin_collection.fonts[0].save(bold)
    mono_collection = TTCollection(str(SYSTEM_MONO))
    mono_collection.fonts[0].save(mono)
    return regular, bold, mono


def parse_chapters(source: str) -> list[Chapter]:
    chapters = [
        Chapter(int(number), title.strip())
        for number, title in re.findall(
            r"^##\s+(\d+)\.\s+(.+)$", source, flags=re.MULTILINE
        )
    ]
    expected = list(range(1, 23))
    actual = [chapter.number for chapter in chapters]
    if actual != expected:
        raise RuntimeError(
            f"Expected chapters 1-22 in order, found chapter numbers {actual}"
        )
    return chapters


def markdown_body(source: str, source_directory: Path) -> str:
    first_chapter = re.search(r"^##\s+1\.\s+", source, flags=re.MULTILINE)
    if first_chapter is None:
        raise RuntimeError("The Markdown source has no chapter 1")
    chapter_source = source[first_chapter.start() :]
    rendered = markdown.markdown(
        chapter_source,
        extensions=("extra", "fenced_code", "tables", "toc", "sane_lists"),
    )
    soup = BeautifulSoup(rendered, "html.parser")
    transform_mermaid(soup)
    transform_admonitions(soup)
    transform_image_tables(soup, source_directory)
    transform_standalone_images(soup, source_directory)
    renumber_figures(soup)
    group_diagrams_with_labels(soup)
    classify_tables_and_code(soup)
    transform_chapters(soup)
    return str(soup)


def transform_mermaid(soup: BeautifulSoup) -> None:
    for code in list(soup.select("pre > code.language-mermaid")):
        definition = html.unescape(code.get_text())
        if definition.lstrip().startswith("sequenceDiagram"):
            code.parent.replace_with(sequence_markup(soup, definition))
            continue
        if definition.startswith("flowchart LR") and definition.count("-->") > 10:
            definition = definition.replace("flowchart LR", "flowchart TB", 1)
        diagram = soup.new_tag("div")
        diagram["class"] = ["mermaid", "diagram-plate"]
        diagram.string = definition
        code.parent.replace_with(diagram)


def sequence_markup(soup: BeautifulSoup, definition: str):
    participants: dict[str, str] = {}
    messages: list[tuple[str, str, str]] = []
    for raw_line in definition.splitlines():
        line = raw_line.strip()
        participant = re.match(r"(?:actor|participant)\s+(\w+)\s+as\s+(.+)", line)
        if participant:
            participants[participant.group(1)] = participant.group(2).strip()
            continue
        message = re.match(r"(\w+)-+>>?(\w+):\s*(.+)", line)
        if message:
            messages.append(
                (
                    participants.get(message.group(1), message.group(1)),
                    participants.get(message.group(2), message.group(2)),
                    message.group(3).strip(),
                )
            )
    if not participants or not messages:
        raise RuntimeError("Cannot parse the sequenceDiagram in the manual")
    plate = soup.new_tag("div")
    plate["class"] = ["sequence-plate"]
    actor_row = soup.new_tag("div")
    actor_row["class"] = ["sequence-actors"]
    for actor in participants.values():
        badge = soup.new_tag("span")
        badge.string = actor
        actor_row.append(badge)
    plate.append(actor_row)
    steps = soup.new_tag("ol")
    for index, (source, target, message) in enumerate(messages, start=1):
        step = soup.new_tag("li")
        number = soup.new_tag("span")
        number["class"] = ["sequence-number"]
        number.string = f"{index:02d}"
        route = soup.new_tag("span")
        route["class"] = ["sequence-route"]
        route.string = f"{source}  →  {target}"
        text = soup.new_tag("strong")
        text.string = message
        step.extend((number, route, text))
        steps.append(step)
    plate.append(steps)
    return plate


def transform_admonitions(soup: BeautifulSoup) -> None:
    for quote in soup.find_all("blockquote"):
        text = quote.get_text(" ", strip=True)
        if text.startswith("[!NOTE]"):
            quote["class"] = ["admonition", "note"]
            label = "COMPATIBILITY NOTE"
            marker = "[!NOTE]"
        elif text.startswith("[!IMPORTANT]"):
            quote["class"] = ["admonition", "important"]
            label = "IMPORTANT"
            marker = "[!IMPORTANT]"
        else:
            continue
        paragraph = quote.find("p")
        if paragraph is None:
            continue
        for child in paragraph.contents:
            if isinstance(child, str) and marker in child:
                child.replace_with(child.replace(marker, "", 1).lstrip())
                break
        badge = soup.new_tag("span")
        badge["class"] = ["admonition-label"]
        badge.string = label
        quote.insert(0, badge)


def resolve_image_source(source_directory: Path, source: str) -> str:
    if source.startswith(("http://", "https://", "data:")):
        return source
    path = (source_directory / source).resolve()
    if not path.is_file():
        raise RuntimeError(f"Referenced image does not exist: {path}")
    return path.as_uri()


def new_figure(
    soup: BeautifulSoup,
    image,
    caption: str,
    figure_number: int,
    *,
    chart: bool = False,
):
    figure = soup.new_tag("figure")
    classes = ["evidence-plate"]
    if chart:
        classes.append("chart-plate")
    figure["class"] = classes
    field = soup.new_tag("div")
    field["class"] = ["image-field"]
    copied = soup.new_tag("img")
    copied.attrs = dict(image.attrs)
    copied["src"] = resolve_image_source(
        PROJECT_ROOT / "docs", str(copied.get("src", ""))
    )
    field.append(copied)
    figure.append(field)
    figcaption = soup.new_tag("figcaption")
    label = soup.new_tag("span")
    label.string = f"FIG {figure_number:02d}"
    figcaption.append(label)
    figcaption.append(caption)
    figure.append(figcaption)
    return figure


def transform_image_tables(soup: BeautifulSoup, source_directory: Path) -> None:
    del source_directory
    figure_number = 20
    for table in list(soup.find_all("table")):
        images = table.find_all("img")
        if not images:
            continue
        headers = [header.get_text(" ", strip=True) for header in table.find_all("th")]
        stack = soup.new_tag("div")
        stack["class"] = ["plate-stack"]
        for index, image in enumerate(images):
            figure_number += 1
            caption = (
                headers[index]
                if index < len(headers)
                else str(image.get("alt", f"Evidence {figure_number}"))
            )
            stack.append(
                new_figure(
                    soup,
                    image,
                    caption,
                    figure_number,
                    chart=True,
                )
            )
        table.replace_with(stack)


def transform_standalone_images(soup: BeautifulSoup, source_directory: Path) -> None:
    del source_directory
    figure_number = 0
    for paragraph in list(soup.find_all("p")):
        children = [
            child
            for child in paragraph.contents
            if not (isinstance(child, str) and not child.strip())
        ]
        if len(children) != 1 or getattr(children[0], "name", None) != "img":
            continue
        image = children[0]
        figure_number += 1
        caption = str(image.get("alt", f"Evidence {figure_number}"))
        paragraph.replace_with(new_figure(soup, image, caption, figure_number))


def renumber_figures(soup: BeautifulSoup) -> None:
    for figure_number, figure in enumerate(soup.find_all("figure"), start=1):
        label = figure.select_one("figcaption > span")
        if label is not None:
            label.string = f"FIG {figure_number:02d}"


def group_diagrams_with_labels(soup: BeautifulSoup) -> None:
    for diagram in list(soup.select(".diagram-plate")):
        label = diagram.find_previous_sibling()
        if label is None or label.name != "p":
            continue
        wrapper = soup.new_tag("div")
        wrapper["class"] = ["diagram-group"]
        label.replace_with(wrapper)
        wrapper.append(label)
        wrapper.append(diagram.extract())


def classify_tables_and_code(soup: BeautifulSoup) -> None:
    for table in soup.find_all("table"):
        first_row = table.find("tr")
        columns = len(first_row.find_all(["th", "td"])) if first_row else 0
        classes = list(table.get("class", []))
        if columns >= 4:
            classes.append("wide-table")
        elif columns == 2:
            classes.append("two-column-table")
        table["class"] = classes
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        language = "TEXT"
        if code:
            for css_class in code.get("class", []):
                if css_class.startswith("language-"):
                    language = css_class.removeprefix("language-").upper()
                    break
        pre["data-language"] = language


def part_for_chapter(number: int) -> tuple[int, str, str, range, str]:
    for part in PARTS:
        if number in part[3]:
            return part
    raise RuntimeError(f"No part is defined for chapter {number}")


def part_divider_html(part: tuple[int, str, str, range, str]) -> str:
    number, english, chinese, chapter_range, color = part
    start = chapter_range.start
    end = chapter_range.stop - 1
    ticks = "".join(
        f'<i style="height:{8 + (index % 4) * 4}mm"></i>' for index in range(16)
    )
    return f"""
<section class="part-divider special-page" style="--part-color:{color}">
  <div class="part-index">PART {number:02d}</div>
  <div class="part-axis">{ticks}</div>
  <div class="part-copy">
    <p>XAEROSPACE / USER MANUAL / {VERSION}</p>
    <h2>{html.escape(english)}</h2>
    <h3>{html.escape(chinese)}</h3>
  </div>
  <div class="part-range">
    <span>CHAPTERS</span><strong>{start:02d}—{end:02d}</strong>
  </div>
</section>
"""


def transform_chapters(soup: BeautifulSoup) -> None:
    for heading in list(soup.find_all("h2")):
        match = re.match(r"(\d+)\.\s+(.+)", heading.get_text(" ", strip=True))
        if not match:
            continue
        number = int(match.group(1))
        title = match.group(2)
        part = part_for_chapter(number)
        if number == part[3].start:
            fragment = BeautifulSoup(part_divider_html(part), "html.parser")
            heading.insert_before(fragment.section)
        classes = ["chapter-heading", f"part-{part[0]}"]
        if number in CHAPTER_PAGE_BREAKS:
            classes.append("chapter-break")
        replacement = soup.new_tag("section")
        replacement["class"] = classes
        replacement["id"] = f"chapter-{number:02d}"
        kicker = soup.new_tag("p")
        kicker["class"] = ["chapter-kicker"]
        kicker.string = f"CHAPTER {number:02d} · {part[1]}"
        replacement.append(kicker)
        number_node = soup.new_tag("span")
        number_node["class"] = ["chapter-number"]
        number_node.string = f"{number:02d}"
        replacement.append(number_node)
        title_node = soup.new_tag("h2")
        title_node.string = title
        replacement.append(title_node)
        heading.replace_with(replacement)


def toc_markup(chapters: list[Chapter], page_map: dict[int, int] | None) -> str:
    groups: list[str] = []
    for part_number, english, chinese, chapter_range, color in PARTS:
        rows: list[str] = []
        for chapter in chapters:
            if chapter.number not in chapter_range:
                continue
            page_number = (
                f"{page_map[chapter.number]:02d}"
                if page_map and chapter.number in page_map
                else "··"
            )
            rows.append(
                f"""
<a class="toc-row" href="#{chapter.anchor}">
  <span class="toc-number">{chapter.number:02d}</span>
  <span class="toc-title">{html.escape(chapter.title)}</span>
  <span class="toc-page">{page_number}</span>
</a>
"""
            )
        groups.append(
            f"""
<section class="toc-part" style="--part-color:{color}">
  <header>
    <span>PART {part_number:02d}</span>
    <strong>{html.escape(english)}</strong>
    <small>{html.escape(chinese)}</small>
  </header>
  <div>{"".join(rows)}</div>
</section>
"""
        )
    return "".join(groups)


def quick_start_markup(source: str) -> str:
    task_match = re.search(
        r"## 从这里开始\s+(?P<table>\|.+?\n)(?=最快启动路径：)",
        source,
        flags=re.DOTALL,
    )
    command_match = re.search(
        r"最快启动路径：\s+```bash\s+(?P<commands>.*?)```",
        source,
        flags=re.DOTALL,
    )
    if task_match is None or command_match is None:
        raise RuntimeError("Cannot extract the quick-start section")
    rows = []
    for line in task_match.group("table").splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        target = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cells[1])
        rows.append((cells[0], target))
    task_rows = "".join(
        f"""
<div class="quick-task">
  <span>{index:02d}</span><strong>{html.escape(task)}</strong>
  <small>{html.escape(target)}</small>
</div>
"""
        for index, (task, target) in enumerate(rows, start=1)
    )
    commands = html.escape(command_match.group("commands").strip())
    return f"""
<section class="quick-start special-page">
  <div class="quick-rail">
    <span>START</span>
    <strong>03</strong>
    <small>任务导航</small>
  </div>
  <div class="quick-main">
    <header>
      <p>XAEROSPACE / FIRST RUN</p>
      <h2>从任务出发，<br>不是从参数出发。</h2>
    </header>
    <div class="quick-tasks">{task_rows}</div>
    <div class="quick-command">
      <span>FAST PATH / 3 COMMANDS</span>
      <pre>{commands}</pre>
    </div>
  </div>
</section>
"""


def render_document(
    *,
    source: str,
    chapters: list[Chapter],
    body: str,
    fonts: dict[str, str],
    page_map: dict[int, int] | None,
) -> str:
    template = DOCUMENT_TEMPLATE
    replacements = {
        "__CJK_REGULAR__": fonts["cjk_regular"],
        "__CJK_BOLD__": fonts["cjk_bold"],
        "__LATIN_REGULAR__": fonts["latin_regular"],
        "__LATIN_BOLD__": fonts["latin_bold"],
        "__MONO__": fonts["mono"],
        "__VERSION__": VERSION,
        "__TOC__": toc_markup(chapters, page_map),
        "__QUICK_START__": quick_start_markup(source),
        "__BODY__": body,
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def materialize_html(chrome: Path, html_path: Path, output: Path) -> None:
    command = [
        str(chrome),
        "--headless",
        "--disable-gpu",
        "--allow-file-access-from-files",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=15000",
        "--dump-dom",
        html_path.as_uri(),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    rendered = completed.stdout
    if (
        completed.returncode != 0
        or 'data-rendered="true"' not in rendered
        or rendered.count("<svg") < 3
    ):
        raise RuntimeError(
            "Chrome failed to materialize Mermaid diagrams:\n"
            + completed.stdout[-2_000:]
            + completed.stderr[-2_000:]
        )
    soup = BeautifulSoup(rendered, "html.parser")
    for script in soup.find_all("script"):
        script.decompose()
    output.write_text(str(soup), encoding="utf-8")


def print_pdf(chrome: Path, html_path: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    static_html = html_path.with_name(html_path.stem + ".static.html")
    materialize_html(chrome, html_path, static_html)
    command = [
        str(chrome),
        "--headless",
        "--disable-gpu",
        "--allow-file-access-from-files",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=15000",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={output}",
        static_html.as_uri(),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not output.is_file():
        raise RuntimeError(
            "Chrome failed to print the manual:\n" + completed.stdout + completed.stderr
        )


def collect_chapter_pages(path: Path) -> dict[int, int]:
    page_map: dict[int, int] = {}
    with fitz.open(path) as document:
        for page_index, page in enumerate(document):
            text = page.get_text()
            for match in re.finditer(r"CHAPTER\s+(\d{2})\b", text):
                number = int(match.group(1))
                page_map.setdefault(number, page_index + 1)
    missing = set(range(1, 23)) - set(page_map)
    if missing:
        raise RuntimeError(f"Chapter headings missing from rendered PDF: {missing}")
    return page_map


def stamp_pdf(raw_path: Path, output: Path) -> None:
    with fitz.open(raw_path) as document:
        current_chapter = 1
        total = len(document)
        special_markers = (
            "XAEROSPACE\nUSER MANUAL",
            "CONTENTS / FLIGHT MANUAL",
            "从任务出发",
            "PART 01",
            "PART 02",
            "PART 03",
            "PART 04",
        )
        for index, page in enumerate(document):
            text = page.get_text()
            page_chapters = re.findall(r"CHAPTER\s+(\d{2})\b", text)
            display_chapter = (
                int(page_chapters[0]) if page_chapters else current_chapter
            )
            if index < 3 or any(marker in text for marker in special_markers):
                continue
            width = page.rect.width
            height = page.rect.height
            shape = page.new_shape()
            shape.draw_line((48, 31), (width - 48, 31))
            shape.draw_line((48, height - 30), (width - 48, height - 30))
            shape.finish(color=(0.50, 0.56, 0.59), width=0.45)
            shape.commit(overlay=True)
            page.insert_text(
                (48, 23),
                f"XAEROSPACE / USER MANUAL / {VERSION}",
                fontsize=6.4,
                fontname="cour",
                color=(0.20, 0.25, 0.27),
                overlay=True,
            )
            chapter_label = (
                f"CH {display_chapter:02d} / "
                f"{CHAPTER_LABELS.get(display_chapter, 'REFERENCE')}"
            )
            page.insert_textbox(
                fitz.Rect(width - 260, 14, width - 48, 27),
                chapter_label,
                fontsize=6.4,
                fontname="cour",
                color=(0.12, 0.37, 0.80),
                align=fitz.TEXT_ALIGN_RIGHT,
                overlay=True,
            )
            if page_chapters:
                current_chapter = int(page_chapters[-1])
            page.insert_text(
                (48, height - 18),
                "INTELLIGENCE COMPILES / PHYSICS VERIFIES",
                fontsize=6.2,
                fontname="cour",
                color=(0.34, 0.39, 0.42),
                overlay=True,
            )
            page.insert_textbox(
                fitz.Rect(width - 180, height - 26, width - 48, height - 12),
                f"P. {index + 1:02d} / {total:02d}",
                fontsize=6.4,
                fontname="cour",
                color=(0.12, 0.37, 0.80),
                align=fitz.TEXT_ALIGN_RIGHT,
                overlay=True,
            )
        metadata = document.metadata
        metadata.update(
            {
                "title": f"Xaerospace v{VERSION} 使用手册",
                "author": "Xaerospace",
                "subject": "LLM-first aerospace simulation user manual",
                "keywords": "Xaerospace, RocketPy, TudatPy, JSBSim, Basilisk",
                "creator": "Xaerospace Markdown publishing pipeline",
            }
        )
        document.set_metadata(metadata)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        document.save(output, garbage=4, deflate=True, clean=True)


def validate_pdf(path: Path) -> None:
    with fitz.open(path) as document:
        text = "\n".join(page.get_text() for page in document)
        han_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        image_count = sum(len(page.get_images(full=True)) for page in document)
        blank_pages: list[int] = []
        overflow_pages: list[int] = []
        for index, page in enumerate(document):
            page_text = page.get_text().strip()
            drawings = len(page.get_drawings())
            special_page = any(
                marker in page_text
                for marker in (
                    "PART 01",
                    "PART 02",
                    "PART 03",
                    "PART 04",
                )
            )
            if len(page_text) < 80 and drawings < 5 and not special_page:
                blank_pages.append(index + 1)
            for block in page.get_text("blocks"):
                x0, y0, x1, y1 = block[:4]
                if (
                    x0 < -1
                    or y0 < -1
                    or x1 > page.rect.width + 1
                    or y1 > page.rect.height + 1
                ):
                    overflow_pages.append(index + 1)
                    break
        if han_count < 6_500:
            raise RuntimeError(
                f"PDF Chinese text extraction is incomplete: {han_count} Han chars"
            )
        if image_count < 12:
            raise RuntimeError(f"PDF contains only {image_count} image objects")
        if blank_pages:
            raise RuntimeError(f"PDF contains near-blank pages: {blank_pages}")
        if overflow_pages:
            raise RuntimeError(f"PDF text exceeds page bounds: {overflow_pages}")
        print(
            f"manual built: {path}\n"
            f"pages: {len(document)}\n"
            f"bytes: {path.stat().st_size}\n"
            f"extractable Han characters: {han_count}\n"
            f"image objects: {image_count}\n"
            "near-blank pages: 0\n"
            "out-of-bounds text pages: 0"
        )


def main() -> int:
    args = parse_args()
    source_path = args.source.resolve()
    output_path = args.output.resolve()
    chrome = locate_chrome(args.chrome)
    source = source_path.read_text(encoding="utf-8")
    chapters = parse_chapters(source)
    body = markdown_body(source, source_path.parent)
    with tempfile.TemporaryDirectory(prefix="xaerospace-manual-") as temp_name:
        temp = Path(temp_name)
        cjk_regular, cjk_bold = prepare_cjk_fonts(temp)
        latin_regular, latin_bold, mono = prepare_latin_fonts(temp)
        fonts = {
            "cjk_regular": data_uri(cjk_regular),
            "cjk_bold": data_uri(cjk_bold),
            "latin_regular": data_uri(latin_regular),
            "latin_bold": data_uri(latin_bold),
            "mono": data_uri(mono),
        }
        draft_html = temp / "draft.html"
        draft_pdf = temp / "draft.pdf"
        draft_html.write_text(
            render_document(
                source=source,
                chapters=chapters,
                body=body,
                fonts=fonts,
                page_map=None,
            ),
            encoding="utf-8",
        )
        print_pdf(chrome, draft_html, draft_pdf)
        page_map = collect_chapter_pages(draft_pdf)
        final_html = temp / "final.html"
        raw_pdf = temp / "raw.pdf"
        final_document = render_document(
            source=source,
            chapters=chapters,
            body=body,
            fonts=fonts,
            page_map=page_map,
        )
        final_html.write_text(final_document, encoding="utf-8")
        if args.keep_html:
            keep_html = args.keep_html.resolve()
            keep_html.parent.mkdir(parents=True, exist_ok=True)
            keep_html.write_text(final_document, encoding="utf-8")
        print_pdf(chrome, final_html, raw_pdf)
        stamp_pdf(raw_pdf, output_path)
    validate_pdf(output_path)
    return 0


DOCUMENT_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xaerospace v__VERSION__ 使用手册</title>
<style>
@font-face {
  font-family: "XA CJK";
  src: url("__CJK_REGULAR__") format("truetype");
  font-weight: 400;
}
@font-face {
  font-family: "XA CJK";
  src: url("__CJK_BOLD__") format("truetype");
  font-weight: 700;
}
@font-face {
  font-family: "XA Sans";
  src: url("__LATIN_REGULAR__") format("truetype");
  font-weight: 400;
}
@font-face {
  font-family: "XA Sans";
  src: url("__LATIN_BOLD__") format("truetype");
  font-weight: 700;
}
@font-face {
  font-family: "XA Mono";
  src: url("__MONO__") format("truetype");
  font-weight: 400;
}
:root {
  --paper: #f4f7f6;
  --white: #ffffff;
  --ink: #171d20;
  --muted: #68767c;
  --line: #c9d1d2;
  --blue: #1f5eff;
  --blue-soft: #e8efff;
  --teal: #00a190;
  --teal-soft: #e3f4f1;
  --red: #f04438;
  --red-soft: #ffe9e6;
  --night: #111a1e;
}
@page {
  size: A4;
  margin: 17mm 18mm 19mm;
  background: var(--paper);
}
@page special {
  size: A4;
  margin: 0;
  background: var(--paper);
}
* {
  box-sizing: border-box;
}
html, body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "XA Sans", "XA CJK", sans-serif;
  font-size: 10pt;
  line-height: 1.64;
  -webkit-font-smoothing: antialiased;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
.special-page {
  page: special;
  position: relative;
  width: 210mm;
  height: 297mm;
  overflow: hidden;
  break-before: page;
  break-after: page;
}
.cover {
  background: var(--paper);
}
.cover-rail {
  position: absolute;
  inset: 0 auto 0 0;
  width: 25mm;
  background: var(--blue);
}
.cover-rail span {
  position: absolute;
  left: 7mm;
  bottom: 18mm;
  color: white;
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: 1mm;
  writing-mode: vertical-rl;
  transform: rotate(180deg);
}
.cover-top {
  position: absolute;
  top: 18mm;
  left: 42mm;
  right: 18mm;
  display: flex;
  justify-content: space-between;
  padding-bottom: 4mm;
  border-bottom: .4mm solid var(--ink);
  font: 7pt/1.4 "XA Mono", monospace;
  letter-spacing: .4mm;
}
.cover-xa {
  position: absolute;
  top: 51mm;
  left: 38mm;
  color: var(--ink);
  font: 700 92pt/.78 "XA Sans", sans-serif;
  letter-spacing: -5mm;
}
.cover-xa em {
  color: var(--blue);
  font-style: normal;
}
.cover-title {
  position: absolute;
  left: 42mm;
  bottom: 54mm;
  width: 105mm;
}
.cover-title p {
  margin: 0 0 6mm;
  color: var(--red);
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: .8mm;
}
.cover-title h1 {
  margin: 0;
  font-size: 29pt;
  line-height: 1.08;
  letter-spacing: 0;
}
.cover-title h2 {
  margin: 5mm 0 0;
  color: var(--muted);
  font-size: 13pt;
  font-weight: 400;
}
.trajectory {
  position: absolute;
  top: 55mm;
  right: 22mm;
  bottom: 26mm;
  width: 38mm;
  border-left: .55mm solid var(--ink);
}
.trajectory::after {
  content: "";
  position: absolute;
  left: -2.5mm;
  top: -1mm;
  width: 4.5mm;
  height: 4.5mm;
  background: var(--red);
  transform: rotate(45deg);
}
.trajectory i {
  position: absolute;
  left: -1.5mm;
  width: 3mm;
  height: 3mm;
  border: .4mm solid var(--blue);
  border-radius: 50%;
  background: var(--paper);
}
.trajectory i::after {
  content: attr(data-label);
  position: absolute;
  left: 6mm;
  top: -1mm;
  width: 28mm;
  color: var(--muted);
  font: 6.4pt/1.25 "XA Mono", monospace;
}
.trajectory i:nth-child(1) { bottom: 8%; }
.trajectory i:nth-child(2) { bottom: 33%; }
.trajectory i:nth-child(3) { bottom: 62%; }
.trajectory i:nth-child(4) { bottom: 87%; }
.cover-stats {
  position: absolute;
  left: 42mm;
  right: 65mm;
  bottom: 20mm;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-top: .25mm solid var(--line);
}
.cover-stats div {
  padding-top: 4mm;
}
.cover-stats strong {
  display: block;
  color: var(--blue);
  font: 700 18pt/1 "XA Sans", sans-serif;
}
.cover-stats span {
  color: var(--muted);
  font: 6.5pt/1.4 "XA Mono", monospace;
}
.toc {
  padding: 16mm 18mm 18mm;
}
.toc-banner {
  height: 48mm;
  margin: -16mm -18mm 9mm;
  padding: 16mm 18mm 0;
  color: white;
  background: var(--ink);
}
.toc-banner p {
  margin: 0;
  color: #94a4aa;
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: .7mm;
}
.toc-banner h2 {
  margin: 5mm 0 0;
  font-size: 28pt;
  line-height: 1;
}
.toc-banner span {
  color: #8fabff;
}
.toc-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6mm;
}
.toc-part {
  min-height: 101mm;
  padding: 5mm;
  border-top: 2mm solid var(--part-color);
  background: white;
}
.toc-part header {
  display: grid;
  grid-template-columns: 18mm 1fr;
  margin-bottom: 3mm;
  padding-bottom: 3mm;
  border-bottom: .25mm solid var(--line);
}
.toc-part header span {
  grid-row: 1 / 3;
  color: var(--part-color);
  font: 7pt/1.3 "XA Mono", monospace;
}
.toc-part header strong {
  font-size: 10pt;
}
.toc-part header small {
  color: var(--muted);
  font-size: 7.5pt;
}
.toc-row {
  display: grid;
  grid-template-columns: 8mm 1fr 8mm;
  gap: 2mm;
  min-height: 7.2mm;
  align-items: center;
  color: inherit;
  border-bottom: .2mm solid #e1e6e7;
  text-decoration: none;
}
.toc-number,
.toc-page {
  color: var(--part-color);
  font: 7pt/1 "XA Mono", monospace;
  font-variant-numeric: tabular-nums;
}
.toc-page {
  text-align: right;
}
.toc-title {
  font-size: 7.7pt;
  font-weight: 700;
  line-height: 1.2;
}
.quick-start {
  display: grid;
  grid-template-columns: 48mm 1fr;
}
.quick-rail {
  padding: 18mm 10mm;
  color: white;
  background: var(--blue);
}
.quick-rail span,
.quick-rail small {
  display: block;
  font: 7pt/1.4 "XA Mono", monospace;
  letter-spacing: .6mm;
}
.quick-rail strong {
  display: block;
  margin: 18mm 0 4mm;
  font: 700 50pt/.9 "XA Sans", sans-serif;
}
.quick-main {
  padding: 18mm 18mm 16mm 12mm;
}
.quick-main header p {
  margin: 0;
  color: var(--red);
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: .6mm;
}
.quick-main header h2 {
  margin: 5mm 0 9mm;
  font-size: 25pt;
  line-height: 1.12;
}
.quick-tasks {
  border-top: .55mm solid var(--ink);
}
.quick-task {
  display: grid;
  grid-template-columns: 9mm 1fr 35mm;
  gap: 3mm;
  align-items: center;
  min-height: 16mm;
  border-bottom: .22mm solid var(--line);
}
.quick-task span {
  color: var(--blue);
  font: 7pt/1 "XA Mono", monospace;
}
.quick-task strong {
  font-size: 9pt;
}
.quick-task small {
  color: var(--muted);
  font-size: 7.2pt;
  text-align: right;
}
.quick-command {
  margin-top: 8mm;
  padding: 5mm;
  color: white;
  background: var(--night);
}
.quick-command > span {
  color: #7fa0ff;
  font: 6.5pt/1 "XA Mono", monospace;
  letter-spacing: .5mm;
}
.quick-command pre {
  margin: 4mm 0 0;
  padding: 0;
  color: white;
  background: transparent;
  border: 0;
}
.part-divider {
  color: white;
  background: var(--ink);
}
.part-divider::after {
  content: "";
  position: absolute;
  inset: 0 0 0 auto;
  width: 43mm;
  background: var(--part-color);
}
.part-index {
  position: absolute;
  top: 18mm;
  left: 18mm;
  color: #9ca8ad;
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: .8mm;
}
.part-copy {
  position: absolute;
  left: 18mm;
  bottom: 58mm;
  width: 135mm;
}
.part-copy p {
  color: var(--part-color);
  font: 7pt/1 "XA Mono", monospace;
  letter-spacing: .7mm;
}
.part-copy h2 {
  margin: 7mm 0 0;
  font-size: 38pt;
  line-height: .95;
}
.part-copy h3 {
  margin: 5mm 0 0;
  color: #b9c4c8;
  font-size: 15pt;
  font-weight: 400;
}
.part-range {
  position: absolute;
  left: 18mm;
  bottom: 20mm;
}
.part-range span {
  display: block;
  color: #87969c;
  font: 6.5pt/1 "XA Mono", monospace;
}
.part-range strong {
  display: block;
  margin-top: 2mm;
  color: var(--part-color);
  font-size: 18pt;
}
.part-axis {
  position: absolute;
  z-index: 2;
  right: 13mm;
  top: 18mm;
  bottom: 18mm;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: center;
}
.part-axis i {
  display: block;
  width: .35mm;
  background: white;
}
.manual {
  page: auto;
}
.chapter-heading {
  position: relative;
  display: grid;
  grid-template-columns: 22mm 1fr;
  grid-template-rows: auto auto;
  column-gap: 7mm;
  min-height: 31mm;
  margin: 2mm 0 8mm;
  padding: 5mm 0 4mm;
  border-top: .7mm solid var(--ink);
  border-bottom: .22mm solid var(--line);
  break-after: avoid;
}
.chapter-heading.chapter-break {
  break-before: page;
}
.chapter-kicker {
  grid-column: 2;
  margin: 0 0 2mm;
  color: var(--part-color, var(--blue));
  font: 6.5pt/1 "XA Mono", monospace;
  letter-spacing: 0;
}
.chapter-number {
  grid-row: 1 / 3;
  align-self: end;
  color: var(--part-color, var(--blue));
  font: 700 29pt/.8 "XA Sans", sans-serif;
  font-variant-numeric: tabular-nums;
}
.chapter-heading h2 {
  grid-column: 2;
  margin: 0;
  font-size: 19pt;
  line-height: 1.08;
}
.chapter-heading.part-1 { --part-color: var(--blue); }
.chapter-heading.part-2 { --part-color: var(--teal); }
.chapter-heading.part-3 { --part-color: var(--red); }
.chapter-heading.part-4 { --part-color: var(--ink); }
h3 {
  margin: 8mm 0 3mm;
  font-size: 13.2pt;
  line-height: 1.25;
  break-after: avoid;
}
h4 {
  margin: 6mm 0 2mm;
  color: var(--blue);
  font-size: 10.5pt;
  break-after: avoid;
}
p {
  margin: 0 0 3.4mm;
  orphans: 3;
  widows: 3;
}
ul, ol {
  margin: 1.5mm 0 4mm;
  padding-left: 6mm;
}
li {
  margin: 1.15mm 0;
}
a {
  color: inherit;
  text-decoration: none;
}
code {
  padding: .25mm 1mm;
  color: #144ec6;
  background: var(--blue-soft);
  border-radius: .7mm;
  font-family: "XA Mono", "XA CJK", monospace;
  font-size: .87em;
}
pre {
  position: relative;
  margin: 4mm 0 6mm;
  padding: 8mm 5mm 4.5mm;
  color: #edf4f6;
  background: var(--night);
  border-left: 1.2mm solid var(--blue);
  font: 8.1pt/1.52 "XA Mono", "XA CJK", monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  break-inside: avoid;
}
pre::before {
  content: attr(data-language);
  position: absolute;
  top: 2.6mm;
  left: 5mm;
  color: #7f9eff;
  font: 6pt/1 "XA Mono", monospace;
  letter-spacing: .4mm;
}
pre code {
  padding: 0;
  color: inherit;
  background: transparent;
  font: inherit;
}
.admonition {
  position: relative;
  margin: 5mm 0 6mm;
  padding: 6mm 6mm 5mm;
  border-top: 1.4mm solid var(--red);
  background: var(--red-soft);
  break-inside: avoid;
}
.admonition.note {
  border-color: var(--teal);
  background: var(--teal-soft);
}
.admonition-label {
  display: block;
  margin-bottom: 2mm;
  color: var(--red);
  font: 6.4pt/1 "XA Mono", monospace;
  letter-spacing: .45mm;
}
.admonition.note .admonition-label {
  color: var(--teal);
}
.admonition p {
  margin: 0;
}
table {
  width: 100%;
  margin: 4mm 0 6mm;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 8pt;
  line-height: 1.4;
}
thead {
  display: table-header-group;
}
tr {
  break-inside: avoid;
}
th {
  padding: 2.6mm 2.3mm;
  color: white;
  background: var(--ink);
  border-right: .2mm solid #465156;
  text-align: left;
  font-weight: 700;
}
td {
  padding: 2.3mm;
  vertical-align: top;
  border-bottom: .2mm solid var(--line);
  overflow-wrap: anywhere;
}
tbody tr:nth-child(even) td {
  background: white;
}
.wide-table {
  font-size: 7pt;
  line-height: 1.32;
}
.two-column-table td:last-child,
.two-column-table th:last-child {
  width: 34%;
}
.evidence-plate {
  margin: 6mm 0 8mm;
  break-inside: avoid;
}
.image-field {
  display: grid;
  place-items: center;
  padding: 2.5mm;
  background: white;
  border: .25mm solid #aeb9bc;
}
.evidence-plate img {
  display: block;
  max-width: 100%;
  max-height: 183mm;
  object-fit: contain;
}
.evidence-plate figcaption {
  display: grid;
  grid-template-columns: 14mm 1fr;
  margin-top: 2.5mm;
  color: var(--muted);
  font-size: 7.5pt;
}
.evidence-plate figcaption span {
  color: var(--blue);
  font: 6.5pt/1.4 "XA Mono", monospace;
}
.plate-stack .evidence-plate {
  break-before: page;
}
.chart-plate img {
  width: 100%;
  max-height: 205mm;
}
.diagram-plate {
  display: grid;
  place-items: center;
  min-height: 62mm;
  margin: 5mm 0 7mm;
  padding: 5mm;
  background: white;
  border-top: 1mm solid var(--blue);
  border-bottom: .25mm solid var(--line);
  break-inside: avoid;
}
.diagram-group {
  break-inside: avoid;
}
.diagram-group > p {
  margin-bottom: 3mm;
}
.diagram-plate svg {
  width: 100% !important;
  max-width: 100% !important;
  max-height: 178mm !important;
}
.sequence-plate {
  margin: 5mm 0 7mm;
  padding: 5mm;
  background: white;
  border-top: 1mm solid var(--red);
  break-inside: avoid;
}
.sequence-actors {
  display: flex;
  flex-wrap: wrap;
  gap: 2mm;
  margin-bottom: 4mm;
  padding-bottom: 4mm;
  border-bottom: .25mm solid var(--line);
}
.sequence-actors span {
  padding: 1.3mm 2.2mm;
  color: var(--red);
  background: var(--red-soft);
  font: 6.4pt/1 "XA Mono", "XA CJK", monospace;
}
.sequence-plate ol {
  margin: 0;
  padding: 0;
  list-style: none;
}
.sequence-plate li {
  display: grid;
  grid-template-columns: 8mm 42mm 1fr;
  gap: 3mm;
  align-items: baseline;
  min-height: 8.5mm;
  margin: 0;
  padding: 2mm 0;
  border-bottom: .2mm solid #e1e6e7;
}
.sequence-number {
  color: var(--red);
  font: 6.5pt/1 "XA Mono", monospace;
}
.sequence-route {
  color: var(--muted);
  font: 6.4pt/1.35 "XA Mono", "XA CJK", monospace;
}
.sequence-plate li strong {
  font-size: 8pt;
}
hr {
  height: .25mm;
  margin: 9mm 0;
  border: 0;
  background: var(--line);
}
.end-mark {
  min-height: 118mm;
  margin-top: 12mm;
  padding-top: 5mm;
  border-top: 1.2mm solid var(--blue);
  break-inside: avoid;
}
.end-mark > span {
  color: var(--blue);
  font: 6.5pt/1 "XA Mono", monospace;
  letter-spacing: .5mm;
}
.end-mark div {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  min-height: 96mm;
}
.end-mark strong {
  color: var(--ink);
  font: 700 34pt/.9 "XA Sans", sans-serif;
}
.end-mark small {
  color: var(--muted);
  font: 6.3pt/1.55 "XA Mono", monospace;
  text-align: right;
}
</style>
</head>
<body>
<section class="cover special-page">
  <div class="cover-rail"><span>FLIGHT SYSTEMS / USER MANUAL</span></div>
  <div class="cover-top">
    <span>RELEASE / __VERSION__</span>
    <span>ZH-CN / 2026</span>
  </div>
  <div class="cover-xa"><em>X</em>A</div>
  <div class="cover-title">
    <p>INTELLIGENCE COMPILES · PHYSICS VERIFIES</p>
    <h1>Xaerospace<br>使用手册</h1>
    <h2>可信航空航天仿真工作台</h2>
  </div>
  <div class="trajectory">
    <i data-label="T+000 / LIFTOFF"></i>
    <i data-label="T+155 / STAGE"></i>
    <i data-label="T+405 / INSERTION"></i>
    <i data-label="T+1605 / VERIFY"></i>
  </div>
  <div class="cover-stats">
    <div><strong>04</strong><span>PHYSICS BACKENDS</span></div>
    <div><strong>16</strong><span>TASK VARIANTS</span></div>
    <div><strong>17</strong><span>REFERENCE RUNS</span></div>
  </div>
</section>
<section class="toc special-page">
  <div class="toc-banner">
    <p>CONTENTS / FLIGHT MANUAL</p>
    <h2>任务索引 <span>01—22</span></h2>
  </div>
  <div class="toc-grid">__TOC__</div>
</section>
__QUICK_START__
<main class="manual">__BODY__
  <section class="end-mark">
    <span>END / VALIDATION BOUNDARY</span>
    <div>
      <strong>XA</strong>
      <small>
        FAILURES / EXPLICIT<br>
        SOURCES / TRACEABLE<br>
        PHYSICS / VERIFIED
      </small>
    </div>
  </section>
</main>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.esm.min.mjs";
mermaid.initialize({
  startOnLoad: false,
  theme: "base",
  securityLevel: "loose",
  flowchart: {
    htmlLabels: true,
    curve: "linear",
    nodeSpacing: 28,
    rankSpacing: 36
  },
  sequence: {
    diagramMarginX: 12,
    diagramMarginY: 12,
    actorMargin: 34,
    messageMargin: 24
  },
  themeVariables: {
    fontFamily: "XA Sans, XA CJK, sans-serif",
    fontSize: "12px",
    primaryColor: "#ffffff",
    primaryTextColor: "#171d20",
    primaryBorderColor: "#1f5eff",
    lineColor: "#68767c",
    secondaryColor: "#e8efff",
    tertiaryColor: "#e3f4f1",
    actorBkg: "#ffffff",
    actorBorder: "#1f5eff",
    signalColor: "#171d20",
    signalTextColor: "#171d20"
  }
});
await mermaid.run({ querySelector: ".mermaid" });
document.documentElement.dataset.rendered = "true";
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
