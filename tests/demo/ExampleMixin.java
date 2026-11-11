package com.example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(net.minecraft.world.entity.Entity.class)
public abstract class ExampleMixin {
    // clean: cancellable + cancel
    @Inject(method = "tick", at = @At("HEAD"), cancellable = true)
    private void onTick(CallbackInfo ci) {
        ci.cancel();
    }

    // M002: cancels but cancellable = true is missing
    @Inject(method = "move", at = @At("HEAD"))
    private void onMove(CallbackInfo ci) {
        ci.cancel();
    }

    // M005: handler returns void but the redirected call returns int
    @Redirect(method = "getX", at = @At(value = "INVOKE", target = "Lnet/Mth;->floor(D)I"))
    private void onFloor(double value) {
    }

    // M001: no CallbackInfo parameter
    @Inject(method = "remove", at = @At("TAIL"))
    private void onRemove() {
    }

    // M003: cancellable but never cancels
    @Inject(method = "save", at = @At("HEAD"), cancellable = true)
    private void onSave(CallbackInfo ci) {
    }
}
