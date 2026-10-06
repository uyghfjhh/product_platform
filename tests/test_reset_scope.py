import yaml
from platform_app.pgcluster_reset import _deployment_roots


def test_reset_never_claims_binary_home_parent_or_remote_data(tmp_path):
    for name in ('selected','other','remote'):
        directory=tmp_path/name/'data';directory.mkdir(parents=True);(directory/'PG_VERSION').write_text('15')
    config=tmp_path/'cluster.yaml'
    config.write_text(yaml.safe_dump({'hosts':{'local':{'address':'127.0.0.1'},'remote':{'address':'192.168.0.15'}},
      'postgresql_installations':{'shared':{'home':'/usr/local/shared-postgres'}},
      'instances':{'selected':{'host':'local','data_dir':str(tmp_path/'selected/data')},
                   'other':{'host':'local','data_dir':str(tmp_path/'other/data')},
                   'remote':{'host':'remote','data_dir':str(tmp_path/'remote/data')}}}))
    assert _deployment_roots(config,{'selected','remote'})==[str(tmp_path/'selected/data')]
