import ast
import tempfile
import unittest
from pathlib import Path

from lunete import personal

PKG = Path(personal.__file__).parent


def lunete_imports(module: str) -> set[str]:
    """Every lunete module `module` reaches through its imports, followed transitively."""
    seen, todo = set(), [module]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        file = PKG / (name.split(".", 1)[1].replace(".", "/") + ".py") if "." in name else PKG / "__init__.py"
        if not file.exists():
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    base = "lunete" + ("." + node.module if node.module else "")
                    todo.append(base)
                    if not node.module:
                        todo.extend("lunete." + a.name for a in node.names)
                    else:
                        todo.extend(f"{base}.{a.name}" for a in node.names if (PKG / node.module / f"{a.name}.py").exists())
                elif node.module and node.module.startswith("lunete"):
                    todo.append(node.module)
            elif isinstance(node, ast.Import):
                todo.extend(a.name for a in node.names if a.name.startswith("lunete"))
    return seen


class PersonalStaysPersonal(unittest.TestCase):
    def test_the_sync_code_never_imports_the_personal_store(self):
        reached = lunete_imports("lunete.sync")
        self.assertNotIn("lunete.personal", reached)
        self.assertIn("lunete.auth", reached)  # the walk does follow imports

    def test_the_walk_would_catch_it(self):
        self.assertIn("lunete.graph", lunete_imports("lunete.personal"))

    def test_opens_under_the_data_home_and_makes_it_once(self):
        home = Path(tempfile.mkdtemp())
        s = personal.open_store(home)
        self.assertEqual(s.path, home / "graph" / "personal.db")
        self.assertEqual(s.space, "personal")
        org = s.org
        s.close()
        again = personal.open_store(home)
        self.assertEqual(again.org, org)


if __name__ == "__main__":
    unittest.main()
