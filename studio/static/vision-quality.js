// Engineering quality gates; thresholds are not camera/model accuracy guarantees.
export function imageQuality(pixels, w, h) {
  if (
    !Number.isInteger(w) ||
    !Number.isInteger(h) ||
    w < 16 ||
    h < 16 ||
    w > 512 ||
    h > 512 ||
    pixels.length !== w * h * 4
  )
    throw Error("图像尺寸/像素长度非法");
  const gray = (x, y) => {
    const i = (y * w + x) * 4;
    return 0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2];
  };
  let sum = 0,
    square = 0,
    edge = 0,
    n = 0;
  for (let y = 1; y < h - 1; y += 2)
    for (let x = 1; x < w - 1; x += 2) {
      const g = gray(x, y);
      sum += g;
      square += g * g;
      edge += Math.abs(
        4 * g -
          gray(x - 1, y) -
          gray(x + 1, y) -
          gray(x, y - 1) -
          gray(x, y + 1),
      );
      n++;
    }
  const mean = sum / n,
    deviation = Math.sqrt(Math.max(0, square / n - mean * mean)),
    sharpness = edge / n,
    reason =
      mean < 15
        ? "画面过暗"
        : mean > 240
          ? "画面过曝"
          : deviation < 12
            ? "遮挡或缺少纹理"
            : sharpness < 3
              ? "画面可能模糊"
              : "可分析";
  return { ok: reason === "可分析", reason, mean, deviation, sharpness };
}
