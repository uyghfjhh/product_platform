# Demo product package

This package is a minimal onboarding example. It does not connect to a database
or deploy a cluster. Its single case checks that the platform passed an
environment ID, then writes a step and an evidence attachment.

The integration points are `product.yaml` (catalog and action), `provider.py`
(case discovery and task command), `cases.py` (SDK case), and `frontend.ts`
(test page registration). Create a demo environment with deployment target
`demo.local`, bind its `smoke` profile, then run `smoke.context`.

For a new product, copy the package structure and replace the action, profile,
case and Provider logic. A real database product also needs its own topology,
fixtures and assertions; this example does not validate those integrations.
