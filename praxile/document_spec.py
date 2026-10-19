"""Small declarative document format; no template code or external resources."""
import json


def docx_spec_prompt():
    return (
        'create_docx.document must contain ONLY {"blocks":[...]}. No title, metadata, '
        'styles, header, footer, or page settings outside blocks. Supported blocks: '
        '{"type":"paragraph","text":"..."}, {"type":"heading","text":"...","level":1}, '
        '{"type":"bullet","text":"..."}, {"type":"table","rows":[["cell","cell"]]}, '
        '{"type":"page_break"}. Heading level is 1..3; tables must be rectangular strings '
        '(1..100 rows, 1..12 columns). Maximum 200 blocks / 256 KiB spec. '
        'Use an existing parent directory and a new .docx path; existing files are never overwritten.'
    )


def validate_docx_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"blocks"}:
        raise ValueError("document spec requires only blocks")
    blocks = spec["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 200:
        raise ValueError("document requires 1..200 blocks")
    if len(json.dumps(spec, ensure_ascii=False).encode()) > 256 * 1024:
        raise ValueError("document spec exceeds 256 KiB")
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("block must be an object")
        kind = block.get("type")
        if kind in {"paragraph", "heading", "bullet"}:
            allowed = {"type", "text", "level"} if kind == "heading" else {"type", "text"}
            if set(block) - allowed or not isinstance(block.get("text"), str) or len(block["text"]) > 20000:
                raise ValueError("invalid text block")
            if kind == "heading" and (type(block.get("level", 1)) is not int or not 1 <= block.get("level", 1) <= 3):
                raise ValueError("heading level must be 1..3")
        elif kind == "page_break":
            if set(block) != {"type"}:
                raise ValueError("invalid page break")
        elif kind == "table":
            rows = block.get("rows")
            if (set(block) != {"type", "rows"} or not isinstance(rows, list) or not 1 <= len(rows) <= 100
                    or not isinstance(rows[0], list) or not 1 <= len(rows[0]) <= 12):
                raise ValueError("table requires 1..100 rows and 1..12 columns")
            if any(not isinstance(row, list) or len(row) != len(rows[0])
                   or any(not isinstance(cell, str) or len(cell) > 4000 for cell in row) for row in rows):
                raise ValueError("invalid table cells")
        else:
            raise ValueError("unsupported document block")
    return spec
