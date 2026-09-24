class RwToggleCase(object):
    def __init__(
        self,
        name,
        rel_dir,
        script,
        topology,
        route_mode,
        driver,
        summary,
        batch=0,
        report_level="advanced",
        runner="legacy_shell",
        native_kind=None,
        native_driver=None,
    ):
        self.name = name
        self.rel_dir = rel_dir
        self.script = script
        self.topology = topology
        self.route_mode = route_mode
        self.driver = driver
        self.summary = summary
        self.batch = batch
        self.report_level = report_level
        self.runner = runner
        self.native_kind = native_kind
        self.native_driver = native_driver
