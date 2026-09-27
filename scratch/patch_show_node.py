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
    content = content.replace("fdd.show_node_info(false, false)", "fdd.show_node_info(true, false)")
    p.write_text(content, encoding='utf-8')
    py_compile.compile(filepath, doraise=True)
    print("Compiled successfully:", filepath)
