#!/usr/bin/env python3
"""Regenerate docs/audit.png and docs/clean.png. Requires Pillow.

Run: python tools/make_demo.py && python tools/make_shots.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mixinlint as ml
import render_terminal as rt

CLEAN = '''package com.example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(net.minecraft.world.entity.Entity.class)
public abstract class CleanMixin {
    @Inject(method = "getX", at = @At("HEAD"), cancellable = true)
    private void onGetX(CallbackInfoReturnable<Integer> cir) {
        cir.setReturnValue(0);
    }
}
'''


def render(name, ansi, cmd, title):
    lines = rt.parse_ansi(ansi)
    rt.render(lines, title, cmd, os.path.join(ROOT, "docs", name + ".png"))


def main():
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    stats, issues = ml.audit(os.path.join(ROOT, "tests", "demo"))
    render("audit", ml.report(stats, issues, True, False),
           "python mixinlint.py tests/demo", "mixinlint — audit")
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "CleanMixin.java"), "w", encoding="utf-8") as fh:
            fh.write(CLEAN)
        stats, issues = ml.audit(tmp)
        render("clean", ml.report(stats, issues, True, False),
               "python mixinlint.py src/main/java", "mixinlint — clean")


if __name__ == "__main__":
    main()