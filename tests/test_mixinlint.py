import contextlib
import io
import json
import os
import tempfile
import unittest

import mixinlint as ml


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def mixin(body, cls="TestMixin"):
    return (
        "package com.example.mixin;\n"
        "import org.spongepowered.asm.mixin.Mixin;\n"
        "import org.spongepowered.asm.mixin.injection.Inject;\n"
        "import org.spongepowered.asm.mixin.injection.Redirect;\n"
        "import org.spongepowered.asm.mixin.injection.At;\n"
        "import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;\n"
        "import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;\n"
        "@Mixin(Target.class)\n"
        "public class " + cls + " {\n" + body + "\n}\n"
    )


def codes(issues):
    return sorted(i.code for i in issues)


class JavaChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_src(self, body, cls="TestMixin"):
        write(self.root, "TestMixin.java", mixin(body, cls))
        stats, issues = ml.audit(self.root)
        return stats, issues

    def test_clean_inject(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertEqual(issues, [])

    def test_clean_returnable(self):
        _, issues = self.run_src(
            '@Inject(method = "get", at = @At("HEAD"), cancellable = true)\n'
            "private void onGet(CallbackInfoReturnable<Integer> cir) { cir.setReturnValue(5); }")
        self.assertEqual(issues, [])

    def test_m001_missing_ci(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            "private void onTick() { }")
        self.assertIn("M001", codes(issues))

    def test_m002_cancel_without_cancellable(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertIn("M002", codes(issues))

    def test_m003_cancellable_unused(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { }")
        self.assertIn("M003", codes(issues))
        self.assertEqual([i.severity for i in issues if i.code == "M003"], [ml.WARN])

    def test_m004_setreturn_on_plain_ci(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.setReturnValue(0); }")
        self.assertIn("M004", codes(issues))

    def test_m005_redirect_mismatch(self):
        _, issues = self.run_src(
            '@Redirect(method = "foo", at = @At(value = "INVOKE", target = "Lnet/Foo;->bar(I)I"))\n'
            "private void onBar(Foo instance, int x) { }")
        self.assertIn("M005", codes(issues))
        self.assertNotIn("M001", codes(issues))

    def test_m005_redirect_match(self):
        _, issues = self.run_src(
            '@Redirect(method = "foo", at = @At(value = "INVOKE", target = "Lnet/Foo;->bar(I)I"))\n'
            "private int onBar(Foo instance, int x) { return x; }")
        self.assertNotIn("M005", codes(issues))

    def test_m005_wrapper_compatible(self):
        _, issues = self.run_src(
            '@Redirect(method = "foo", at = @At(value = "INVOKE", target = "Lnet/Foo;->bar()I"))\n'
            "private Integer onBar(Foo instance) { return 1; }")
        self.assertNotIn("M005", codes(issues))

    def test_m006_overwrite_with_ci(self):
        _, issues = self.run_src(
            "@Overwrite\n"
            "public void tick(CallbackInfo ci) { }")
        self.assertIn("M006", codes(issues))

    def test_m007_non_private_handler(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "public void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertIn("M007", codes(issues))

    def test_m008_shadow_with_body(self):
        _, issues = self.run_src(
            "@Shadow\n"
            "public int compute() { return 1; }")
        self.assertIn("M008", codes(issues))

    def test_m008_shadow_abstract_ok(self):
        _, issues = self.run_src(
            "@Shadow\n"
            "public abstract int compute();")
        self.assertNotIn("M008", codes(issues))

    def test_m009_missing_method(self):
        _, issues = self.run_src(
            '@Inject(at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertIn("M009", codes(issues))

    def test_m010_missing_at(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertIn("M010", codes(issues))

    def test_comment_cancel_ignored(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            "private void onTick(CallbackInfo ci) { /* ci.cancel(); */ }")
        self.assertNotIn("M002", codes(issues))

    def test_string_cancel_ignored(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            'private void onTick(CallbackInfo ci) { String s = "ci.cancel()"; }')
        self.assertNotIn("M002", codes(issues))

    def test_local_capture_ci_not_last(self):
        _, issues = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci, int local, String other) { ci.cancel(); }")
        self.assertEqual(issues, [])

    def test_non_mixin_ignored(self):
        write(self.root, "Plain.java", "public class Plain { void x() {} }")
        stats, issues = ml.audit(self.root)
        self.assertEqual(stats["mixins"], 0)
        self.assertEqual(issues, [])

    def test_stats_counts(self):
        stats, _ = self.run_src(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }")
        self.assertEqual(stats["mixins"], 1)
        self.assertEqual(stats["handlers"], 1)


CONFIG = {
    "package": "com.example.mixin",
    "compatibilityLevel": "JAVA_17",
    "refmap": "example.refmap.json",
    "mixins": ["TestMixin"],
}


class ConfigChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cfg(self, cfg):
        write(self.root, "example.mixins.json", json.dumps(cfg))
        return ml.audit(self.root)

    def test_clean_config(self):
        _, issues = self.run_cfg(CONFIG)
        self.assertEqual(issues, [])

    def test_c001_missing_package(self):
        cfg = dict(CONFIG)
        del cfg["package"]
        _, issues = self.run_cfg(cfg)
        self.assertIn("C001", codes(issues))

    def test_c002_missing_compat(self):
        cfg = dict(CONFIG)
        del cfg["compatibilityLevel"]
        _, issues = self.run_cfg(cfg)
        self.assertIn("C002", codes(issues))

    def test_c006_bad_compat(self):
        cfg = dict(CONFIG, compatibilityLevel="JAVA_99")
        _, issues = self.run_cfg(cfg)
        self.assertIn("C006", codes(issues))

    def test_c003_empty(self):
        cfg = dict(CONFIG, mixins=[])
        _, issues = self.run_cfg(cfg)
        self.assertIn("C003", codes(issues))

    def test_c005_duplicate(self):
        cfg = dict(CONFIG, mixins=["TestMixin", "TestMixin"])
        _, issues = self.run_cfg(cfg)
        self.assertIn("C005", codes(issues))

    def test_c004_missing_class(self):
        cfg = dict(CONFIG, mixins=["GhostMixin"])
        _, issues = self.run_cfg(cfg)
        self.assertIn("C004", codes(issues))

    def test_c007_no_refmap(self):
        cfg = dict(CONFIG)
        del cfg["refmap"]
        _, issues = self.run_cfg(cfg)
        self.assertIn("C007", codes(issues))

    def test_bad_json(self):
        write(self.root, "example.mixins.json", "{ not json")
        _, issues = ml.audit(self.root)
        self.assertIn("C000", codes(issues))


class ReportAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return ml.main(args)

    def test_json_report(self):
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }"))
        stats, issues = ml.audit(self.root)
        out = json.loads(ml.report(stats, issues, False, True))
        self.assertEqual(out["errors"], 1)
        self.assertEqual(out["issues"][0]["code"], "M002")

    def test_clean_report_text(self):
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }"))
        stats, issues = ml.audit(self.root)
        self.assertIn("clean", ml.report(stats, issues, False, False))

    def test_exit_clean(self):
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }"))
        self.assertEqual(self._run([self.root, "--no-color"]), 0)

    def test_exit_error(self):
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"))\n'
            "private void onTick(CallbackInfo ci) { ci.cancel(); }"))
        self.assertEqual(self._run([self.root, "--no-color"]), 1)

    def test_exit_warning_only_zero(self):
        write(self.root, "TestMixin.java", mixin(
            '@Inject(method = "tick", at = @At("HEAD"), cancellable = true)\n'
            "private void onTick(CallbackInfo ci) { }"))
        self.assertEqual(self._run([self.root, "--no-color"]), 0)
        self.assertEqual(self._run([self.root, "--no-color", "--strict"]), 1)

    def test_exit_bad_path(self):
        self.assertEqual(self._run([os.path.join(self.root, "nope"), "--no-color"]), 2)


if __name__ == "__main__":
    unittest.main()