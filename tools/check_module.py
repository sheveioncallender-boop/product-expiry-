"""Dependency-free syntax, manifest, XML and workflow-boundary checks."""
import ast
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "spx_product_expiry"
manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text())
assert manifest["depends"] == ["product_expiry"]
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
                            assert ast.literal_eval(value.value) in ("stock.lot", "product.template")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "sudo", "Expiry screens must honor native access rules"
for path in MODULE.rglob("*.xml"):
    ET.parse(path)
print("PASS: Python syntax, XML syntax, manifest files, and no stock/POS workflow overrides")
