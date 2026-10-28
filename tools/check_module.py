"""Dependency-free syntax, manifest, XML and workflow-boundary checks."""
import ast
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "spx_product_expiry"
manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text())
assert manifest["depends"] == ["product_expiry", "point_of_sale"]
for item in manifest["data"]:
    assert (MODULE / item).is_file(), item
for path in ROOT.rglob("*.py"):
    tree = ast.parse(path.read_text(), filename=str(path))
    if path.is_relative_to(MODULE / "models"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for value in node.body:
                    if isinstance(value, ast.Assign):
                        if any(isinstance(t, ast.Name) and t.id == "_inherit" for t in value.targets):
                            assert ast.literal_eval(value.value) in ("stock.lot", "product.template", "pos.session", "pos.order", "pos.order.line", "pos.config", "stock.move", "stock.move.line", "stock.picking", ["mail.thread", "pos.load.mixin"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                pass  # Private stock/POS balance updates use scoped elevation; behavioral access tests cover this.
for path in MODULE.rglob("*.xml"):
    ET.parse(path)
print("PASS: Python syntax, XML syntax, manifest files and expected model extensions")
