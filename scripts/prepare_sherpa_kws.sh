#!/usr/bin/env bash
set -e

MODEL_NAME="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
MODEL_ROOT="models/kws"
MODEL_DIR="${MODEL_ROOT}/${MODEL_NAME}"
ARCHIVE="${MODEL_ROOT}/${MODEL_NAME}.tar.bz2"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${MODEL_NAME}.tar.bz2"

KWS_CONFIG_DIR="configs/kws"
KEYWORDS_RAW="${KWS_CONFIG_DIR}/keywords_raw.txt"
KEYWORDS_TXT="${KWS_CONFIG_DIR}/keywords.txt"

mkdir -p "${MODEL_ROOT}"
mkdir -p "${KWS_CONFIG_DIR}"

if ! command -v sherpa-onnx-cli >/dev/null 2>&1; then
  echo "ERROR: sherpa-onnx-cli 不存在，请先执行：pip install sherpa-onnx" >&2
  exit 1
fi

if [ ! -d "${MODEL_DIR}" ]; then
  echo "[prepare] 模型目录不存在，开始下载：${MODEL_URL}"
  if command -v wget >/dev/null 2>&1; then
    wget -O "${ARCHIVE}" "${MODEL_URL}" || {
      echo "ERROR: 下载失败。可手动执行：" >&2
      echo "  wget -O ${ARCHIVE} ${MODEL_URL}" >&2
      exit 1
    }
  elif command -v curl >/dev/null 2>&1; then
    curl -L -o "${ARCHIVE}" "${MODEL_URL}" || {
      echo "ERROR: 下载失败。可手动执行：" >&2
      echo "  curl -L -o ${ARCHIVE} ${MODEL_URL}" >&2
      exit 1
    }
  else
    echo "ERROR: 未找到 wget 或 curl。请手动下载：" >&2
    echo "  ${MODEL_URL}" >&2
    exit 1
  fi

  tar -xjf "${ARCHIVE}" -C "${MODEL_ROOT}"
  rm -f "${ARCHIVE}"
else
  echo "[prepare] 模型已存在，跳过下载：${MODEL_DIR}"
fi

if [ ! -f "${KEYWORDS_RAW}" ] || [ "${FORCE:-0}" = "1" ]; then
  cat > "${KEYWORDS_RAW}" <<'EOF'
小航小航 :2.5 #0.35 @小航小航
EOF
  echo "[prepare] 已写入：${KEYWORDS_RAW}"
else
  echo "[prepare] ${KEYWORDS_RAW} 已存在，未覆盖。需要覆盖请执行：FORCE=1 bash scripts/prepare_sherpa_kws.sh"
fi

sherpa-onnx-cli text2token \
  --tokens "${MODEL_DIR}/tokens.txt" \
  --tokens-type ppinyin \
  "${KEYWORDS_RAW}" \
  "${KEYWORDS_TXT}"

echo "[prepare] model_dir: ${MODEL_DIR}"
echo "[prepare] keywords_raw: ${KEYWORDS_RAW}"
echo "[prepare] keywords_txt: ${KEYWORDS_TXT}"

