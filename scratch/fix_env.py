with open('/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress/framework/environment.py', 'r', encoding='utf-8') as f:
    c = f.read()
target = 'return process.stdout.strip().splitlines()[-1].strip()'
replacement = '''lines = process.stdout.strip().splitlines()
        return lines[-1].strip() if lines else ""'''
if target in c:
    c = c.replace(target, replacement, 1)
    with open('/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress/framework/environment.py', 'w', encoding='utf-8') as f:
        f.write(c)
    print('Updated fbase_regress/framework/environment.py')
