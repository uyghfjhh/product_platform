import importlib
import json
from pathlib import Path
from platform_app.report_views import describe_report
from platform_regress.reporting.description import declared_description


def test_every_fbase_case_has_behavior_and_step_expectations():
    module=importlib.import_module('products.fbase-database.cases')
    assert len(module._DEFINITIONS)==228
    for definition in module._DEFINITIONS.values():
        assert definition['purpose'] and definition['pass_criteria']
        assert all(isinstance(i,int) and 1<=i<=len(definition['steps']) for i in definition['pass_criteria'])
        for step in definition['steps']:
            assert step['intent'] in ('prepare','action','verify','cleanup')
            assert step.get('expected') is not None
    definition=module._DEFINITIONS['mmr.background.maintenance_lifecycle']
    assert '正常启动' in definition['purpose'] and '不再重启' in definition['purpose']
    assert '已退出' in definition['final_state']
    assert definition['steps'][0]['intent']=='prepare'
    assert definition['steps'][6]['intent']=='action'


def test_archived_description_is_bound_to_execution_and_target(tmp_path):
    base=tmp_path/'artifacts'/'run';base.mkdir(parents=True)
    description={'target':'case','purpose':'archive-time purpose','final_state':'expected end'}
    (base/'case-description.json').write_text(json.dumps(description))
    payload={'target':'case','execution_id':'run','verdict':'PASS'}
    assert describe_report(tmp_path,payload)['case_description']['purpose']=='archive-time purpose'
    assert describe_report(tmp_path,{**payload,'execution_id':'other'})['case_description'] is None
    assert describe_report(tmp_path,{**payload,'target':'wrong'})['case_description'] is None


def test_description_does_not_change_assertions():
    definition={'id':'case','purpose':'verify a negative boundary','steps':[{'title':'拒绝未授权操作','intent':'verify','expected':'permission denied','assertion':{'type':'sql_error','sqlstate':'42501'}}]}
    before=json.dumps(definition,sort_keys=True)
    description=declared_description(definition)
    assert description['steps'][0]['expected']=='permission denied'
    assert json.dumps(definition,sort_keys=True)==before
