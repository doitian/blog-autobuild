# Blog Autobuild

A Python-based tool to automate the conversion of Obsidian-flavored Markdown files into Hugo-compatible content for static site generation.

## Overview

This project provides a robust script (`x.py`) that transforms a personal knowledge base (managed in Obsidian) into a public blog format. It handles the intricacies of Obsidian's specific syntax—like wiki-links, callouts, and embedded images—and converts them into Hugo shortcodes and standard Markdown.

## Key Features

- **Smart Parsing**: Uses a state-machine approach to handle complex Markdown structures contextually.
- **Obsidian Compatibility**:
    - **Wiki-links**: Converts `[[Internal Link]]` to Hugo `relref` or standard HTML links.
    - **Embeds**: Transforms `![[Image]]` into optimized Hugo shortcodes (`{{< image-card >}}`, `{{< gallery-card >}}`).
    - **Callouts**: Maps Obsidian callouts (`> [!INFO]`) to Hugo shortcodes.
    - **Block References**: Supports anchoring to specific blocks (`^block-id`).
- **Mathematical Notation**: Full support for KaTeX inline (`$x$`) and block (`$$...$$`) math.
- **Metadata Management**: Automatically processes front matter, manages aliases, and generates breadcrumb navigation.

## Prerequisites

- **Python 3.14+**
- **uv**: An extremely fast Python package manager.
- **mise** (Optional but recommended): For managing environment tasks and tool versions.

## Setup & Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd blog-autobuild
    ```

2.  **Install Dependencies:**
    This project uses `uv` for dependency management.
    ```bash
    uv sync
    ```

## Configuration

The script relies on environment variables to locate your source files and destination directory.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `KB_SRC_DIR` | Path to your Obsidian vault (source Markdown files). | `~/Dropbox/Brain/output` |
| `KB_CONTENT_DIR` | Path to your Hugo site's `content` directory. | `~/codebase/iany.me/content` |

You can set these in your shell or within a `.env` file if your environment supports it.

## Usage

### Running Tests

To ensure the converter is working as expected, run the included test suite (located in `test-vectors/`):

```bash
# Using uv directly
uv run python x.py

# OR using mise
mise run test
```

### Building the Site

To perform a full conversion of your knowledge base:

```bash
# Using uv directly
uv run python x.py run

# OR using mise
mise run build
```

This command scans the `KB_SRC_DIR` for valid publishable content (specifically files starting with `§ ` within a `§ Blog` folder structure), converts them, and outputs the results to `KB_CONTENT_DIR`.

### Watch Mode

The script is designed to work with `watchexec` for incremental builds. It detects environment variables like `WATCHEXEC_CREATED_PATH` to only rebuild files that have changed.

## Project Structure

- `x.py`: The core conversion logic.
- `pyproject.toml`: Project dependencies and metadata (managed by `uv`).
- `mise.toml`: Task definitions for `mise`.
- `test-vectors/`: Input/Output pairs for regression testing.

## License

[MIT](LICENSE)
