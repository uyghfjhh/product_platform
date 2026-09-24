"""Global-cache case result assembly."""


def set_report_blocks(rt, verification_checks=None, business_summary=None, key_evidence=None):
    if verification_checks is not None:
        rt.summary["verification_checks"] = verification_checks
    if business_summary is not None:
        rt.summary["business_summary_lines"] = business_summary
    if key_evidence is not None:
        rt.summary["key_evidence_lines"] = key_evidence
