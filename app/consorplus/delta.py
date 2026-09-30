"""ASP.NET AJAX partial-rendering ("delta") responses.

An async postback answers with a sequence of `length|type|id|content|` records. `length` is
the content length in UTF-16 code units (it is a .NET/JavaScript string length).
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup

from app.consorplus.errors import ParseError


class DeltaParseError(ParseError):
    pass


@dataclass(frozen=True)
class DeltaNode:
    type: str
    id: str
    content: str


def _advance_utf16(text: str, start: int, units: int) -> int:
    """Index reached after consuming `units` UTF-16 code units from `start`."""
    end = start + units
    if all(ord(char) <= 0xFFFF for char in text[start:end]):
        return end
    index, remaining = start, units
    while remaining > 0 and index < len(text):
        remaining -= 2 if ord(text[index]) > 0xFFFF else 1
        index += 1
    if remaining != 0:
        raise DeltaParseError("La longitud del delta corta un carácter por la mitad")
    return index


def _read_field(text: str, start: int) -> tuple[str, int]:
    end = text.find("|", start)
    if end < 0:
        raise DeltaParseError(f"Falta un separador '|' a partir de la posición {start}")
    return text[start:end], end + 1


def parse_delta(text: str) -> list[DeltaNode]:
    nodes: list[DeltaNode] = []
    index = 0
    while index < len(text):
        length_text, index = _read_field(text, index)
        if not length_text.isdigit():
            raise DeltaParseError(f"Longitud inválida en el delta: {length_text[:20]!r}")
        node_type, index = _read_field(text, index)
        node_id, index = _read_field(text, index)
        end = _advance_utf16(text, index, int(length_text))
        if end >= len(text) or text[end] != "|":
            raise DeltaParseError(f"Contenido del nodo {node_type}|{node_id} mal delimitado")
        nodes.append(DeltaNode(node_type, node_id, text[index:end]))
        index = end + 1
    if not nodes:
        raise DeltaParseError("Respuesta delta vacía")
    return nodes


def apply_delta(html: str, nodes: list[DeltaNode]) -> str:
    """Return `html` with the update panels, hidden fields and form action from `nodes`."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    for node in nodes:
        if node.type == "updatePanel":
            panel = soup.find(id=node.id)
            if panel is None:
                raise DeltaParseError(f"El panel {node.id!r} no está en la página")
            panel.clear()
            panel.append(BeautifulSoup(node.content, "html.parser"))
        elif node.type == "hiddenField":
            field = soup.find("input", attrs={"name": node.id})
            if field is None:
                if form is None:
                    raise DeltaParseError("La página no tiene <form>")
                field = soup.new_tag("input", attrs={"type": "hidden", "name": node.id})
                form.append(field)
            field["value"] = node.content
        elif node.type == "formAction" and form is not None:
            form["action"] = node.content
    return str(soup)
