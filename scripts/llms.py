"""Publish the docs as Markdown for language models, after `zensical build`.

Copies each page in the nav to `<site>/<page>.md` and writes `<site>/llms.txt` (an index, per
https://llmstxt.org) and `<site>/llms-full.txt` (every page in nav order).
"""

import argparse
import shutil
import tomllib
from collections.abc import Iterator
from pathlib import Path

Nav = list[dict[str, "str | Nav"]]


def pages(nav: Nav, section: str = "") -> Iterator[tuple[str, str, str]]:
    for entry in nav:
        for title, target in entry.items():
            if isinstance(target, str):
                yield section, title, target
            else:
                yield from pages(target, title)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("zensical.toml"))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()

    project = tomllib.loads(args.config.read_text())["project"]
    base = project["site_url"].rstrip("/")
    if not args.site.is_dir():
        raise SystemExit(f"{args.site} does not exist. Run `zensical build` first.")

    index = [f"# {project['site_name']}", "", f"> {project['site_description']}"]
    full: list[str] = []
    current = None
    for section, title, page in pages(project["nav"]):
        source = args.docs / page
        shutil.copyfile(source, args.site / page)
        if section != current:
            index += ["", f"## {section or 'Docs'}", ""]
            current = section
        index.append(f"- [{title}]({base}/{page})")
        full += [f"<!-- {base}/{page} -->", source.read_text().strip(), ""]

    (args.site / "llms.txt").write_text("\n".join(index) + "\n")
    (args.site / "llms-full.txt").write_text("\n".join(full))


if __name__ == "__main__":
    main()
