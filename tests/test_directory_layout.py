"""Persistent state survives clearing disposable run artifacts."""
import json
import shutil

from platform_regress.cli import main
from platform_regress.engine import CaseContext
from test_api import settings_for


def test_ledger_is_outside_artifacts_and_survives_their_deletion(tmp_path):
    settings = settings_for(tmp_path)
    output = settings.artifact_dir('demo', 'env') / 'runs' / 'run-1' / 'cases' / 'demo.case'
    context = CaseContext('demo.case', output, environment={
        'ledger_root': str(settings.resource_dir('demo', 'env')),
    })
    resource = context.ledger.register('directory', path=str(tmp_path / 'owned'))
    shutil.rmtree(settings.output_dir)
    assert context.ledger.entries()[0]['id'] == resource
    assert not context.ledger.root.is_relative_to(settings.output_dir)
    context.ledger.release(resource)


def test_repeated_cli_runs_keep_distinct_evidence_and_shared_failed_state(tmp_path):
    product = tmp_path / 'demo'
    product.mkdir()
    (product / 'product.yaml').write_text("id: demo\nregression_sdk: '2'\n")
    (product / 'cases.py').write_text("class Case:\n def run(self, context): return True\nCASES={'smoke.case': Case()}\n")
    output, state = tmp_path / 'output', tmp_path / 'data'
    args = ['--product-dir', str(product), '--output-dir', str(output), '--state-dir', str(state), 'smoke.case']
    assert main(args) == main(args) == 0
    runs = list((output / 'runs').iterdir())
    assert len(runs) == 2
    assert all((run / 'cases/smoke.case/result.json').is_file() for run in runs)
    assert json.loads((state / 'last_failed.json').read_text())['targets'] == []
    assert (state / 'history.jsonl').is_file()
