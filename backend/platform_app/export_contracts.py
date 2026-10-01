"""Generate TypeScript from the same Pydantic models used as HTTP responses."""

import argparse
import json
from pathlib import Path

from .api import contracts

MODELS = ('Product', 'Environment', 'Task', 'Event', 'Case', 'Result', 'RegressionBinding', 'Action')


def ts_type(schema):
    if '$ref' in schema:
        return schema['$ref'].rsplit('/', 1)[-1]
    if 'anyOf' in schema:
        return ' | '.join(ts_type(part) for part in schema['anyOf'])
    if 'enum' in schema:
        return ' | '.join(json.dumps(value) for value in schema['enum'])
    kind = schema.get('type')
    if kind == 'array':
        return f"Array<{ts_type(schema.get('items', {}))}>"
    if kind == 'object':
        return f"Record<string, {ts_type(schema.get('additionalProperties', {})) if isinstance(schema.get('additionalProperties'), dict) else 'unknown'}>"
    return {'string': 'string', 'integer': 'number', 'number': 'number', 'boolean': 'boolean', 'null': 'null'}.get(kind, 'unknown')


def render_contracts():
    schemas = {}
    for name in MODELS:
        schema = getattr(contracts, name).model_json_schema()
        schemas.update(schema.pop('$defs', {}))
        schemas[name] = schema
    lines = ['// Generated from platform_app.api.contracts. Do not edit.\n']
    for name, schema in sorted(schemas.items()):
        required = set(schema.get('required', []))
        lines.append(f'export type {name} = {{')
        for key, prop in schema.get('properties', {}).items():
            lines.append(f"  {key}{'' if key in required else '?'}: {ts_type(prop)};")
        lines.append('};\n')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = render_contracts()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != content:
            raise SystemExit('API types are stale; regenerate before building')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)


if __name__ == '__main__':
    main()
