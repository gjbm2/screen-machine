#!/usr/bin/env bash
# screen-machine: point ComfyUI at model bundles that RunPod Cached Models put on the host.
# A bundle is a Hugging Face repo laid out like ComfyUI's models/ folder (diffusion_models/,
# text_encoders/, vae/, loras/, ...), or Comfy-Org style with those folders under split_files/.
# Layouts: RunPod docs' /runpod-volume/huggingface-cache/hub/models--org--repo/snapshots/<sha>/,
# and the SDK's /runpod/model-store/huggingface/<org>/<name>/<rev>/. Every cached snapshot found is added to /comfyui/extra_model_paths.yaml, after upstream's
# /runpod-volume entry. Search roots can be overridden with SM_MODEL_ROOTS (space separated).
set -u
ROOTS="${SM_MODEL_ROOTS:-/runpod-volume/huggingface-cache/hub /runpod/model-store/huggingface}"
OUT=/comfyui/extra_model_paths.yaml
FOLDERS="checkpoints clip clip_vision configs controlnet diffusion_models embeddings loras model_patches text_encoders unet upscale_models vae latent_upscale_models"

n=0
seen=" "
for root in $ROOTS; do
  [ -d "$root" ] || continue
  for snap in "$root"/models--*/snapshots/* "$root"/*/*/*; do
    [ -d "$snap" ] || continue
    for base in "$snap" "$snap/split_files"; do
      [ -d "$base" ] || continue
      case "$seen" in *" $base "*) continue ;; esac
      seen="$seen$base "
      entry=""
      for f in $FOLDERS; do
        [ -d "$base/$f" ] && entry="$entry  $f: $f/
"
      done
      [ -n "$entry" ] || continue
      n=$((n+1))
      printf '\nsm_bundle_%d:\n  base_path: %s\n%s' "$n" "$base" "$entry" >> "$OUT"
      echo "sm: models from $base"
    done
  done
done
echo "sm: $n cached model folder(s) registered"
[ "$n" -gt 0 ] || echo "sm: no cached models found under: $ROOTS"
missing=$([ "$n" -gt 0 ] || echo 1)
# what the host gave us, for the logs
for d in /runpod-volume /runpod/model-store; do [ -d "$d" ] && find "$d" -maxdepth 6 2>/dev/null | head -40 | sed 's/^/sm: ls /'; done
# SM_REQUIRE_MODELS=1 (the endpoints set it): a worker without its models must not take jobs, so start.sh exits
[ -n "$missing" ] && [ "${SM_REQUIRE_MODELS:-0}" = "1" ] && exit 2
exit 0
