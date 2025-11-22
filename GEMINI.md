# Blog Autobuild

This project contains a Python script (`x.py`) designed to convert Markdown files from an Obsidian vault structure into a format suitable for a Hugo static site. It handles various Obsidian-specific syntax and customizes the output for the target blog theme.

## Project Structure

*   `x.py`: The main conversion script. It implements a state-machine-based Markdown parser and transformer.
*   `pyproject.toml`: Defines project metadata and Python dependencies, managed by `uv`.
*   `mise.toml`: Configuration for [mise](https://mise.jdx.dev/) (task runner and env manager).
*   `test-vectors/`: Contains pairs of input (`*.in.md`) and expected output (`*.out.md`) files for testing the converter.

## Setup

1.  **Python Environment**: Ensure Python 3 is installed.
2.  **Dependencies**: Dependencies are managed by `uv` and defined in `pyproject.toml`.
    To install them (if not using `mise`):
    ```bash
    uv sync
    ```
    If using `mise`, the virtual environment and dependencies are handled automatically upon running any `mise` task (e.g., `mise run test`).

## Configuration

The script uses environment variables to determine source and destination directories:

*   `KB_SRC_DIR`: Path to the source Markdown files (e.g., your Obsidian vault output).
    *   Default: `~/Dropbox/Brain/output`
*   `KB_CONTENT_DIR`: Path to the destination content directory (e.g., your Hugo `content` folder).
    *   Default: `~/codebase/iany.me/content`

## Usage

### Running Tests

To verify the logic against the test vectors:

```bash
mise run test
```

This parses files in `test-vectors/*.in.md`, compares the output with `*.out.md`, and reports any discrepancies. If a test fails, a `*.real.md` file is generated with the actual output, and a `diff` is shown.

### Building Content

To run the full conversion process:

```bash
python x.py run
```

This scans `KB_SRC_DIR` for files to publish (files starting with `§ ` inside a `§ Blog` directory structure), converts them, and writes them to `KB_CONTENT_DIR`.

The `KB_CONTENT_DIR` is managed by git. Use git to inspect changed files.

### Watch Mode

The script detects `watchexec` environment variables (`WATCHEXEC_CREATED_PATH`, etc.) to perform incremental builds on changed files.

## Key Features

*   **State Machine Parser**: `x.py` uses classes like `StateNormal`, `StateFencedCodeBlock`, `StateCallout` to parse Markdown context-sensitively.
*   **Obsidian Syntax Support**:
    *   Wiki-links (`[[Link]]`) -> Hugo `relref` or standard links.
    *   Embeds (`![[Image]]`) -> Custom Hugo shortcodes (`{{< image-card >}}`, `{{< gallery-card >}}`).
    *   Callouts (`> [!INFO]`) -> Custom Hugo shortcodes (`{{< callout >}}`).
    *   Block references (`^anchor`) -> HTML anchors.
*   **Math**: Support for KaTeX inline (`$x$`) and block (`$$...$$`) math.
*   **Metadata**: Transforms front matter, handling title extraction, aliases, and breadcrumbs.
