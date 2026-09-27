from pathlib import Path
import py_compile

for filepath in [
    '/home/postgres/fly_dev/pgcluster/pgclusterlib/runtime.py',
    '/home/postgres/fly_dev/postgresql_for_fbase_dev/pgcluster/pgclusterlib/runtime.py',
]:
    p = Path(filepath)
    if not p.is_file():
        continue
    content = p.read_text(encoding='utf-8')

    content = content.replace(
        'env_id = f"env_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}"',
        "env_id = f\"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}\""
    )
    content = content.replace(
        'pf.write("\ninclude_if_exists = \'fbase_regress.conf\'\n")',
        "pf.write(\"\\ninclude_if_exists = 'fbase_regress.conf'\\n\")"
    )
    p.write_text(content, encoding='utf-8')
    py_compile.compile(filepath, doraise=True)
    print("Compiled successfully:", filepath)
