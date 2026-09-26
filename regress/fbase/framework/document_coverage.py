import importlib

from framework.errors import ConfigError


def _numeric_point(value):
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


def section_covers_point(section, point):
    for item in (value.strip() for value in section.split(",")):
        if item == point:
            return True
        if "-" not in item:
            continue
        start, end = (value.strip() for value in item.split("-", 1))
        start_parts = _numeric_point(start)
        end_parts = _numeric_point(end)
        point_parts = _numeric_point(point)
        if not start_parts or not end_parts or not point_parts:
            continue
        if (len(start_parts) == len(end_parts) == len(point_parts) and
                start_parts[:-1] == end_parts[:-1] == point_parts[:-1] and
                start_parts[-1] <= point_parts[-1] <= end_parts[-1]):
            return True
    return False


def validate_document_coverage(suite):
    """Ensure every suite case is mapped to an explicit document point."""
    module_name = suite.get("coverage_module")
    if not module_name:
        raise ConfigError("suite %s 未声明 coverage_module" % suite["id"])
    module = importlib.import_module(module_name)
    points = getattr(module, "DOCUMENT_TEST_POINTS", None)
    if not isinstance(points, dict) or not points:
        raise ConfigError("suite %s 的文档覆盖清单为空" % suite["id"])
    exemptions = getattr(module, "DOCUMENT_TEST_POINT_EXEMPTIONS", {})
    if not isinstance(exemptions, dict):
        raise ConfigError("suite %s 的文档测试点豁免清单必须是字典" % suite["id"])
    catalog = {case["id"]: case for case in suite["cases"]}
    mapped = set()
    pending = []
    for document, document_points in points.items():
        if not document_points:
            raise ConfigError("文档未登记测试点: %s" % document)
        for point, case_ids in document_points.items():
            if not isinstance(case_ids, list):
                raise ConfigError("文档测试点映射必须是列表: %s / %s" %
                                  (document, point))
            # A transfer point can contain independent destructive or timing
            # scenarios.  They may be split into dedicated cases so each has
            # its own setup, cleanup, and report; catalog IDs still must be
            # unique, and every case must declare this exact point.
            if len(case_ids) != len(set(case_ids)):
                raise ConfigError("文档测试点重复引用同一 case: %s / %s" %
                                  (document, point))
            if not case_ids:
                reason = exemptions.get(document, {}).get(point, "")
                if not isinstance(reason, str) or not reason.strip():
                    pending.append((document, point))
            for case_id in case_ids:
                if case_id not in catalog:
                    raise ConfigError("文档映射引用未知 case: %s" % case_id)
                if catalog[case_id]["document"] != document:
                    raise ConfigError("case 文档来源不一致: %s" % case_id)
                if not section_covers_point(catalog[case_id]["section"], point):
                    raise ConfigError("case 章节未覆盖文档测试点: %s / %s" %
                                      (case_id, point))
                mapped.add(case_id)
    for document, point_reasons in exemptions.items():
        if document not in points:
            raise ConfigError("豁免引用未知文档: %s" % document)
        if not isinstance(point_reasons, dict):
            raise ConfigError("文档测试点豁免必须是字典: %s" % document)
        for point, reason in point_reasons.items():
            if point not in points[document]:
                raise ConfigError("豁免引用未知文档测试点: %s / %s" %
                                  (document, point))
            if points[document][point]:
                raise ConfigError("已映射测试点不得豁免: %s / %s" %
                                  (document, point))
            if not isinstance(reason, str) or not reason.strip():
                raise ConfigError("测试点豁免原因不能为空: %s / %s" %
                                  (document, point))
    if mapped != set(catalog):
        raise ConfigError("suite %s 存在未映射或多余 case: %s" %
                          (suite["id"], ", ".join(sorted(mapped ^ set(catalog)))))
    if suite.get("coverage_complete") and pending:
        raise ConfigError("suite %s 仍有未覆盖文档测试点: %s" % (
            suite["id"], ", ".join("%s/%s" % item for item in pending)))
    return points
