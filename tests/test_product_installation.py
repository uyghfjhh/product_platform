from dataclasses import replace

import pytest
import yaml
from platform_app.config import Settings, load_settings
from platform_app.filestore import FileStore
from platform_app.product_installation import activate, prepare
from platform_app.product_versions import capture
from platform_app.resources import product_lock


@pytest.fixture
def package(tmp_path, monkeypatch):
    products = tmp_path / "products"
    root = products / "demo"
    root.mkdir(parents=True)
    (root / "product.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "title": "Demo",
                "plugin_api": "v1",
                "provider": "provider.py",
                "versions": [{"id": "1.0"}],
                "capabilities": {},
            }
        )
    )
    code = """VERSION='old'
class Provider:
 def command(self,*args): pass
 def discover(self,*args): return []
 def observe_database(self,*args): return []
 def observe_runtime(self,*args): return []
 def validate_target(self,*args): return True
PROVIDER=Provider()
"""
    (root / "provider.py").write_text(code)
    monkeypatch.setattr(Settings, "products_root", property(lambda _: products))
    settings = replace(
        load_settings(), data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime"
    )
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir)
    artifact = capture(settings, store, "demo", "1.0")
    return settings, store, root, artifact["id"]


def test_product_package_activation_and_build_failure_restore_previous(package):
    settings, store, root, identity = package
    (root / "provider.py").write_text(
        (root / "provider.py").read_text().replace("VERSION='old'", "VERSION='new'")
    )

    def fail():
        raise RuntimeError("front build failed")

    with pytest.raises(RuntimeError):
        activate(settings, store, identity, build=fail)
    assert "VERSION='new'" in (root / "provider.py").read_text()
    result = activate(settings, store, identity, build=lambda: None)
    assert (
        result["status"] == "APPLIED"
        and "VERSION='old'" in (root / "provider.py").read_text()
    )


def test_reviewed_package_staging_cannot_be_changed(package):
    settings, store, root, identity = package
    prepare(settings, store, identity)
    (
        store.root / "product-installation-plans" / identity / "demo/provider.py"
    ).write_text("tampered")
    with pytest.raises(ValueError, match="代码已变化"):
        activate(settings, store, identity, build=lambda: None)
    assert "VERSION='old'" in (root / "provider.py").read_text()


def test_running_product_blocks_activation(package):
    settings, store, root, identity = package
    with product_lock(settings, "demo"):
        with pytest.raises(RuntimeError, match="正在执行"):
            activate(settings, store, identity, build=lambda: None)


def test_frontend_build_uses_staging_before_replacing_live_assets(package):
    settings,store,root,identity=package
    frontend=settings.products_root.parent/'frontend';(frontend/'dist').mkdir(parents=True)
    (frontend/'dist/index.html').write_text('old frontend')
    (frontend/'package.json').write_text('{"scripts":{"build":"node build.cjs"}}')
    (frontend/'build.cjs').write_text("const fs=require('fs');const out=process.argv[process.argv.indexOf('--outDir')+1];if(fs.readFileSync('dist/index.html','utf8')!=='old frontend')process.exit(2);fs.mkdirSync(out,{recursive:true});fs.writeFileSync(out+'/index.html','new frontend');")
    result=activate(settings,store,identity)
    assert result['status']=='APPLIED'
    assert (frontend/'dist/index.html').read_text()=='new frontend'
