#!/usr/bin/env bash
# 一键重新生成所有「可程序化生成」的素材：BGM + 文案校验 + 节拍验收。
# 图片由 AI 图像工具生成，不属于程序化素材；本脚本仅在目标 JPG 缺失时，
# 尝试用已保留的 AI 源图（images/_gen_*/）转换，否则给出明确警告。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/audio" "$HERE/images" "$HERE/texts"

PYTHON="${PYTHON:-python3}"

# 1) BGM：8 秒 / 120 BPM / 每 0.5s 一个清晰鼓点 + 持续低音量底噪。
EXPR='aevalsrc=0.35*sin(2*PI*55*(t-0.5*floor(2*t)))*exp(-25*(t-0.5*floor(2*t)))+0.12*sin(2*PI*440*(t-0.5*floor(2*t)))*exp(-60*(t-0.5*floor(2*t)))+0.05*sin(2*PI*220*t):s=44100:d=8'
ffmpeg -hide_banner -loglevel error -f lavfi -i "$EXPR" \
  -c:a pcm_s16le -ar 44100 -y "$HERE/audio/bgm_120bpm.wav"
echo "OK: 生成 audio/bgm_120bpm.wav"

# 2) 文案校验：必须是合法 JSON 且恰好 10 句。
"$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); n=len(d["captions"]); print("OK: captions.json 含 %d 句" % n); sys.exit(0 if n==10 else 1)' \
  "$HERE/texts/captions.json"

# 3) 若 JPG 缺失，用保留的 AI 源 PNG 转换（不重新调用 AI）。
convert_from_ai_source() {
  local src_dir="$1" target_name="$2"
  local target="$HERE/images/$target_name"
  [ -f "$target" ] && return 0
  local src
  src="$(find "$HERE/images/$src_dir" -maxdepth 1 -type f -name '*.png' -print -quit 2>/dev/null || true)"
  if [ -z "$src" ]; then
    echo "WARN: 缺少 images/$target_name，且未找到 AI 源图；请按 README 用 image_gen 重新生成。" >&2
    return 0
  fi
  ffmpeg -hide_banner -loglevel error -y -i "$src" -q:v 2 "$target"
  echo "OK: 从 AI 源图转换 images/$target_name"
}
convert_from_ai_source _gen_group group_10people.jpg
convert_from_ai_source _gen_person person_ref.jpg

# 4) 节拍验收（第一道关卡）。
set +e
"$PYTHON" "$HERE/verify_beat.py"
rc=$?
set -e
case "$rc" in
  0) echo "OK: 节拍验收 PASS" ;;
  3) echo "WARN: 缺少 librosa，跳过节拍验收（不得视为通过）；安装：python -m pip install librosa" >&2 ;;
  *) echo "FAIL: 节拍验收失败，退出码=$rc" >&2; exit "$rc" ;;
esac

echo "ALL DONE."
