# ==============================================================================
# File:         test_file_names_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Checks that every team-written file and folder carries its
#               writers' initials (BV, CG, DG, SL), so a new file without them
#               is caught before it reaches the grader. Also checks that no
#               guide (.md) or secret file has slipped into the code folder.
# Team-written: All logic written by CG.
# Third-party:  pytest.
# ==============================================================================
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INITIALS = re.compile(r'_(BV|CG|DG|SL)(_(BV|CG|DG|SL))*$')  # one or more owners at the end of the name, e.g. scanner_api_BV_CG

# names a tool needs exactly as they are, so they can't carry initials (Vite, TypeScript, pnpm, pytest, Python packages, git)
FIXED_NAMES = {'index.html', 'package.json', 'pnpm-lock.yaml', 'tsconfig.json', 'vite.config.ts', 'vite-env.d.ts',
               'conftest.py', '__init__.py', '__main__.py', '.gitignore', '.env.example'}  # .env.example: the standard template name
# folders that are shared by the whole team or hold build output, outside fonts, sample files or test pictures
SHARED_FOLDERS = {'src', 'tests', 'dist', 'public', 'assets', 'examples', 'fixtures', 'imports', 'fonts'}
SKIP = {'node_modules', '.venv', '__pycache__', '.pytest_cache', 'runtime', '.git'}


def team_files():
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if SKIP & set(rel.parts):
            continue
        yield path, rel


def stem(name: str) -> str:  # payment_CG.test.mjs -> payment_CG, scanner_api_BV_CG.py -> scanner_api_BV_CG
    return name.split('.')[0] if not name.startswith('.') else name


def test_every_team_written_file_has_initials():
    missing = []
    for path, rel in team_files():
        if not path.is_file() or path.name in FIXED_NAMES:
            continue
        if rel.parts[0] in {'dist', 'public', 'assets', 'examples'} or 'fixtures' in rel.parts or 'imports' in rel.parts:
            continue  # build output, fonts, sample PDFs, test pictures and the logo: nobody's code
        if not INITIALS.search(stem(path.name)):
            missing.append(rel.as_posix())
    assert missing == [], 'add the writer\'s initials to: ' + ', '.join(missing)


def test_every_owned_folder_has_initials():
    missing = []
    for path, rel in team_files():
        if not path.is_dir() or path.name in SHARED_FOLDERS or re.fullmatch(r'[\d-]+', path.name):
            continue  # shared folders, and dated backup folders like 20261014-124901-932694
        if not INITIALS.search(path.name):
            missing.append(rel.as_posix())
    assert missing == [], 'add the owner\'s initials to these folders: ' + ', '.join(missing)


def test_no_guides_or_secrets_in_the_code_folder():
    bad = [rel.as_posix() for path, rel in team_files() if path.is_file() and (
        path.suffix == '.md' or path.suffix in {'.pem', '.key', '.p12'} or path.name == '.env' or path.name.endswith('.env')
        or (path.name.startswith('.env.') and path.name != '.env.example'))]
    assert bad == [], 'these must not be in the code folder: ' + ', '.join(bad)
