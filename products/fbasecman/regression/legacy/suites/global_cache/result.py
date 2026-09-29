"""Global-cache case result assembly."""


import fbasecman_ops as ops
def set_report_blocks(context, verification_checks=None, business_summary=None, key_evidence=None):
    if verification_checks is not None:
        ops.summary["verification_checks"] = verification_checks
    if business_summary is not None:
        ops.summary["business_summary_lines"] = business_summary
    if key_evidence is not None:
        ops.summary["key_evidence_lines"] = key_evidence
