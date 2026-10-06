from copy import deepcopy

import pytest
import yaml
from platform_app.config import load_settings
from platform_app.deployment.adoption import compile_inventory
from platform_app.deployment.models import AdoptionInput, DeploymentSpec
from platform_app.deployment.workbench import compile_spec, validate_config
from pydantic import ValidationError


def inventory():
    members = [dict(node_id=i, node_name='node'+str(i), group_id=1, node_state='ACTIVE',
                    dbname='postgres', endpoint={'host':'127.0.0.1','port':port})
               for i, port in [(1,15432),(2,25432)]]
    groups = [{'group_id':1,'group_name':'existing_group','group_uuid':'existing-uuid'}]
    rows = []
    for port, system, standby in [(15432,'111',False),(25432,'222',False),(15433,'111',True),(25433,'222',True)]:
        rows.append(dict(port=port,system_identifier=system,standby=standby,running=True,
                         mmr_configured=True,home='/opt/pgsql',data_dir='/opt/data/p'+str(port),
                         mmr_nodes=deepcopy(members),mmr_groups=deepcopy(groups),
                         upstream={'host':'127.0.0.1','port':15432 if system=='111' else 25432}))
    return rows


def test_actual_four_node_topology_replaces_fixed_template(tmp_path):
    result = compile_inventory('192.168.0.15',{'user':'postgres'},inventory())
    assert result['targets'] == [{'value':'mmr.discovered_1','label':'多活 · existing_group'}]
    config = yaml.safe_load(result['source_yaml'])
    assert len(config['instances']) == 4
    assert config['streaming_clusters']['stream_1']['standbys'] == [{'instance':'instance_3'}]
    assert config['streaming_clusters']['stream_2']['standbys'] == [{'instance':'instance_4'}]
    settings = load_settings()
    spec = DeploymentSpec(title='existing',product_id='fbase-database',template_id='mmr',
                          mode='adopt',host='192.168.0.15',source_yaml=result['source_yaml'],
                          target=result['targets'][0]['value'])
    compiled, target, _ = compile_spec(settings,spec,'adoption-test')
    assert compiled == config
    path = tmp_path/'pgcluster.yaml'
    path.write_text(result['source_yaml'])
    assert len(validate_config(settings,path,target)['nodes']) == 4


@pytest.mark.parametrize('damage, message',[
    ('missing_member','未探测到'), ('duplicate_primary','多个主节点'),
    ('orphan_standby','唯一对应主库'), ('different_group','不一致'),
    ('offline_mmr','需运行'), ('external_member','跨主机'),
    ('duplicate_path','实际数据目录'),
])
def test_ambiguous_inventory_cannot_be_adopted(damage,message):
    nodes=inventory()
    if damage=='missing_member': nodes=[nodes[0]]
    if damage=='duplicate_primary': nodes[1]['system_identifier']='111'
    if damage=='orphan_standby': nodes[2]['system_identifier']='999'
    if damage=='different_group': nodes[1]['mmr_groups'][0]['group_uuid']='other'
    if damage=='offline_mmr': nodes[0]['running']=False
    if damage=='external_member': nodes[0]['mmr_nodes'][1]['endpoint']['host']='192.168.0.16'
    if damage=='duplicate_path': nodes[1]['data_dir']=nodes[0]['data_dir']
    with pytest.raises(ValueError,match=message):compile_inventory('192.168.0.15',None,nodes)


def test_offline_streaming_uses_control_identity_and_upstream():
    nodes=inventory()[::2]
    for node in nodes:
        node.update(running=False,mmr_configured=False,mmr_groups=[],mmr_nodes=[],warning='offline')
    result=compile_inventory('192.168.0.15',None,nodes)
    assert result['targets'][0]['value']=='streaming.stream_1'
    assert result['warnings']==['offline']


def test_path_validation_prevents_unsafe_inventory_requests():
    for dirs in [[],['/'],['/opt/../data'],['/data/a','/data/a/']]:
        with pytest.raises(ValidationError):AdoptionInput(host='localhost',data_dirs=dirs)


def test_agent_never_returns_connection_secrets():
    from platform_app.deployment.adoption_agent import connection
    assert connection("host=127.0.0.1 port=15432 password='secret value' passfile=/secret") == {'host':'127.0.0.1','port':15432}
