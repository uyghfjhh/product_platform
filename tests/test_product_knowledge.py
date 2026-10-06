from types import SimpleNamespace

import pytest
from platform_app.filestore import FileStore
from platform_app.knowledge import KnowledgeRequest, index, search
from platform_app.product_versions import artifact, capture


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "product"
    root.mkdir()
    (root / "main.py").write_text(
        "def replica_health():\n    password='sensitive-value'\n    return 'ACTIVE'\n"
    )
    manifest = SimpleNamespace(
        id="demo", versions=("1.0",), package_root=root, source_root=None
    )
    monkeypatch.setattr(
        "platform_app.knowledge.discover_products", lambda _: {"demo": manifest}
    )
    monkeypatch.setattr(
        "platform_app.product_versions.discover_products", lambda _: {"demo": manifest}
    )
    return SimpleNamespace(products_root=root), FileStore(tmp_path / "data"), root


def test_knowledge_has_real_line_references_and_rejects_stale_source(context):
    settings, store, root = context
    result = index(settings, store, "demo", "1.0")
    assert result["files"] == 1
    found = search(
        store, "demo", KnowledgeRequest(query="replica_health", version="1.0")
    )
    assert found["version"] == "1.0" and found["evidence"][0]["line_start"] == 1
    assert "sensitive-value" not in found["evidence"][0]["text"]
    (root / "main.py").write_text("def changed(): pass")
    assert (
        search(store, "demo", KnowledgeRequest(query="replica_health", version="1.0"))[
            "evidence"
        ]
        == []
    )


def test_immutable_artifact_is_verified_and_unknown_version_rejected(context):
    settings, store, _ = context
    with pytest.raises(ValueError):
        capture(settings, store, "demo", "2.0")
    row = capture(settings, store, "demo", "1.0")
    path, verified = artifact(store, row["id"])
    assert verified["sha256"] == row["sha256"]
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="完整性"):
        artifact(store, row["id"])


def test_version_indexes_coexist_and_rebuilding_does_not_overwrite_other_versions(context, monkeypatch):
    from platform_app.knowledge import index_path
    settings, store, root = context
    manifest = SimpleNamespace(id='demo', versions=('1.0','2.0'), package_root=root, source_root=None)
    monkeypatch.setattr('platform_app.knowledge.discover_products', lambda _: {'demo':manifest})
    legacy = store.backend.root/'knowledge'/'demo'/'index.json'
    legacy.parent.mkdir(parents=True); legacy.write_text('{"obsolete":true}')
    index(settings,store,'demo','1.0')
    first = index_path(store,'demo','1.0').read_bytes()
    index(settings,store,'demo','2.0')
    assert index_path(store,'demo','1.0').read_bytes() == first
    assert not legacy.exists()
    for version in ('1.0','2.0'):
        assert search(store,'demo',KnowledgeRequest(version=version,query='replica_health'))['evidence']
    (root/'main.py').write_text('def new_replica_health(): return "ACTIVE"')
    index(settings,store,'demo','2.0')
    assert not search(store,'demo',KnowledgeRequest(version='1.0',query='replica_health'))['evidence']
    assert search(store,'demo',KnowledgeRequest(version='2.0',query='replica_health'))['evidence']
    assert index_path(store,'demo','1.0').read_bytes() == first


def fake_model(monkeypatch, responder):
    monkeypatch.setenv('OPENAI_API_KEY','isolated-test-key')
    import openai
    class Client:
        def __init__(self,**kwargs):
            self.responses=SimpleNamespace(parse=responder)
    monkeypatch.setattr(openai,'OpenAI',Client)


def test_grounded_answer_checks_citations_and_sends_no_stored_request(context, monkeypatch):
    from platform_app.knowledge import KnowledgeAnswer, answer
    settings,store,_=context
    index(settings,store,'demo','1.0')
    request=KnowledgeRequest(version='1.0',query='replica_health')
    evidence=search(store,'demo',request)['evidence'][0]
    def respond(**kwargs):
        assert kwargs['store'] is False
        assert kwargs['text_format'] is KnowledgeAnswer
        assert 'sensitive-value' not in str(kwargs['input'])
        return SimpleNamespace(output_parsed=KnowledgeAnswer(answer='函数返回 ACTIVE',evidence_ids=[evidence['id']],unknowns=[]))
    fake_model(monkeypatch,respond)
    result=answer(store,'demo',request)
    assert result['analysis']['evidence_ids'] == [evidence['id']]
    fake_model(monkeypatch,lambda **kwargs: SimpleNamespace(output_parsed=KnowledgeAnswer(answer='unproven',evidence_ids=['invented'],unknowns=[])))
    with pytest.raises(ValueError,match='有效证据'):
        answer(store,'demo',request)


def test_source_change_during_model_request_rejects_stale_answer(context, monkeypatch):
    from platform_app.knowledge import KnowledgeAnswer, answer
    settings,store,root=context
    index(settings,store,'demo','1.0')
    request=KnowledgeRequest(version='1.0',query='replica_health')
    evidence=search(store,'demo',request)['evidence'][0]
    def respond(**kwargs):
        (root/'main.py').write_text('def changed(): pass')
        return SimpleNamespace(output_parsed=KnowledgeAnswer(answer='stale',evidence_ids=[evidence['id']],unknowns=[]))
    fake_model(monkeypatch,respond)
    with pytest.raises(ValueError,match='证据已变化'):
        answer(store,'demo',request)


def test_missing_model_credential_does_not_disable_knowledge_search(context, monkeypatch):
    from platform_app.diagnostics import AIUnavailable
    from platform_app.knowledge import answer, index_path
    settings,store,_=context
    index(settings,store,'demo','1.0')
    request=KnowledgeRequest(version='1.0',query='replica_health')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert search(store,'demo',request)['evidence']
    with pytest.raises(AIUnavailable):answer(store,'demo',request)
    with pytest.raises(ValueError):index_path(store,'../../outside','1.0')
    assert index_path(store,'demo','../version').parent == store.backend.root/'knowledge'/'demo'/'versions'


def test_model_cannot_reuse_citation_from_another_version(context, monkeypatch):
    from platform_app.knowledge import KnowledgeAnswer, answer
    settings,store,root=context
    manifest=SimpleNamespace(id='demo',versions=('1.0','2.0'),package_root=root,source_root=None)
    monkeypatch.setattr('platform_app.knowledge.discover_products',lambda _: {'demo':manifest})
    for version in manifest.versions:index(settings,store,'demo',version)
    request=KnowledgeRequest(version='1.0',query='replica_health')
    other=search(store,'demo',KnowledgeRequest(version='2.0',query='replica_health'))['evidence'][0]
    assert search(store,'demo',request)['evidence'][0]['id'] != other['id']
    fake_model(monkeypatch,lambda **kwargs: SimpleNamespace(output_parsed=KnowledgeAnswer(answer='wrong version',evidence_ids=[other['id']],unknowns=[])))
    with pytest.raises(ValueError,match='有效证据引用'):answer(store,'demo',request)
