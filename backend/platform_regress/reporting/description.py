"""Execution-time case descriptions; never reconstruct archived facts from source."""
import json
from pathlib import Path


def read_description(output_dir, execution_id):
    path=Path(output_dir)/'artifacts'/execution_id/'case-description.json'
    if not path.is_file():
        return None
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):
        return None


def declared_description(definition):
    return {'target':definition['id'],'purpose':definition.get('purpose'),
            'prerequisites':definition.get('prerequisites',[]),
            'pass_criteria':[{'step':i,'title':definition['steps'][i-1]['title'],'expected':definition['steps'][i-1].get('expected')} for i in definition.get('pass_criteria',[])],
            'final_state':definition.get('final_state'),
            'scope_note':definition.get('coverage_note'),
            'steps':[{'order':i,'title':s['title'],'intent':s.get('intent'),'expected':s.get('expected')}
                     for i,s in enumerate(definition.get('steps',[]),1)]}
