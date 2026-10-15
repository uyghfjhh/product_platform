"""Topology version metadata must come from the queried instance catalog."""
import json
import subprocess
import sys
import types
from platform_app.pgcluster_topology import TOPOLOGY_SCRIPT


def test_topology_reads_versions_per_physical_node(monkeypatch, capsys):
    nodes={'pg_1':{'host_config':{'address':'127.0.0.1'},'port':16432,'data_dir':'/data/pg_1','installation':'mmr'},
           'pg_3':{'host_config':{'address':'127.0.0.1'},'port':16433,'data_dir':'/data/pg_3','installation':'mmr'}}
    config=types.SimpleNamespace(validate=lambda _:None, instance=nodes.__getitem__,raw={},
        streaming_clusters={'group':{'primary':'pg_1','standbys':[{'instance':'pg_3'}]}},
        mmr_clusters={'mmr':{'extensions':['fdd_mmr'],'members':{'node1':{'streaming_cluster':'group'}}}},citus_clusters={})
    calls=[]
    class Executor:
        is_local=staticmethod(lambda _:True)
        def run(self,argv,**kwargs):
            port=argv[argv.index('-p')+1];calls.append(port)
            if port=='16433':return subprocess.CompletedProcess(argv,1,'','unreachable')
            return subprocess.CompletedProcess(argv,0,json.dumps({'server_version':'15.15','database_version':'Fbase 15.15','extension_versions':{'fdd_mmr':'2.0'}}),'')
    class Runtime:
        def __init__(self,_):self.executor=Executor()
        def target_instances(self,_):return list(nodes)
        def _bin(self,*_):return '/bin/psql'
    monkeypatch.setitem(sys.modules,'pgclusterlib',types.ModuleType('pgclusterlib'))
    monkeypatch.setitem(sys.modules,'pgclusterlib.config',types.SimpleNamespace(load=lambda _:config))
    monkeypatch.setitem(sys.modules,'pgclusterlib.runtime',types.SimpleNamespace(Runtime=Runtime))
    monkeypatch.setattr(sys,'argv',['probe','config','mmr.mmr'])
    exec(TOPOLOGY_SCRIPT,{})
    result=json.loads(capsys.readouterr().out)['nodes']
    assert sorted(calls)==['16432','16433']
    assert result[0]['server_version']=='15.15'
    assert result[0]['extension_versions']=={'fdd_mmr':'2.0'}
    assert result[1]['server_version'] is None
    assert result[1]['extension_versions']=={}
    assert result[1]['extensions']==['fdd_mmr']
