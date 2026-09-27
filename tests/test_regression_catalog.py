import pytest

from platform_regress import CaseCatalog, CaseContractError


def case(target="sample.case"):
    return {"suite": "sample", "target": target, "title": "Case", "tags": ["smoke"]}


def test_case_catalog_normalizes_product_definitions():
    normalized = CaseCatalog([case()]).to_api()
    assert normalized == [{
        "suite": "sample", "suite_title": "sample", "suite_description": "",
        "target": "sample.case", "name": "case", "title": "Case",
        "core_id": "", "summary": "Case", "enabled": True, "tags": ["smoke"],
    }]


def test_case_catalog_rejects_ambiguous_or_cross_suite_targets():
    with pytest.raises(CaseContractError, match="重复用例目标"):
        CaseCatalog([case(), case()])
    with pytest.raises(CaseContractError, match="不属于套件"):
        CaseCatalog([case("other.case")])
