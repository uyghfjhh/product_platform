"""Small, infrastructure-free regression case for product onboarding."""

from platform_regress.sdk import CaseFailure


class ContextSmokeCase:
    def run(self, context):
        environment_id = context.environment.get("id")
        if not environment_id:
            raise CaseFailure("missing environment id")
        context.step("context", "Environment context available", details={"id": environment_id})
        context.attach_text("environment.txt", environment_id + "\n")
        return True


CASES = {"smoke.context": ContextSmokeCase()}
