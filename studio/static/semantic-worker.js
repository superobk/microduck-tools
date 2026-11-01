import { imageQuality } from "./vision-quality.js";
/* Isolated, local-only inference. This worker has no control transport. */
let pipe = null,
  busy = false,
  sha = null,
  Raw = null;
const labels = [
  "an indoor office or workshop",
  "an indoor living room or home",
  "an outdoor garden forest or green park",
  "an urban street or town",
  "a harbor lake river or waterside",
  "a snowy outdoor landscape",
  "an obscured camera or unrecognizable close-up object",
];
const keys = [
  "workspace",
  "home",
  "greenery",
  "urban",
  "waterside",
  "snow",
  "unknown",
];
onmessage = async ({ data }) => {
  if (busy) {
    postMessage({ type: "error", error: "已有推理任务；旧任务不会排队" });
    return;
  }
  busy = true;
  try {
    if (data.op === "load") {
      sha = data.model_sha;
      const { pipeline, env, RawImage } = await import(
        "/assets/transformers/transformers.min.js"
      );
      Raw = RawImage;
      env.allowRemoteModels = false;
      env.allowLocalModels = true;
      env.localModelPath =
        "/models/" +
        (data.model_revision || "9463a9c508a344c837ffefe9d724f3827bf2dc79") +
        "/";
      env.backends.onnx.wasm.wasmPaths = "/assets/transformers/";
      env.backends.onnx.wasm.numThreads = 1;
      pipe = await pipeline("zero-shot-image-classification", "tinyclip", {
        dtype: "q8",
        device: "wasm",
        local_files_only: true,
        progress_callback: (progress) =>
          postMessage({
            type: "progress",
            status: progress.status,
            progress: progress.progress ?? null,
          }),
      });
      postMessage({ type: "ready", model_sha: sha });
    } else if (data.op === "classify") {
      if (!pipe) throw Error("模型尚未就绪");
      const at = performance.now(),
        quality = imageQuality(
          new Uint8ClampedArray(data.pixels),
          data.width,
          data.height,
        );
      if (!quality.ok) {
        postMessage({
          type: "result",
          frame_id: data.frame_id,
          model_sha: sha,
          label: "unknown",
          support: 0,
          margin: 0,
          quality,
          elapsed_ms: performance.now() - at,
        });
        return;
      }
      const result = await pipe(
        new Raw(new Uint8ClampedArray(data.pixels), data.width, data.height, 4),
        labels,
        { hypothesis_template: "This is {}." },
      );
      const rows = result
        .map((r) => ({
          label: keys[labels.indexOf(r.label)] ?? "unknown",
          score: r.score,
        }))
        .sort((a, b) => b.score - a.score);
      postMessage({
        type: "result",
        frame_id: data.frame_id,
        model_sha: sha,
        label: rows[0].label,
        support: rows[0].score,
        margin: rows[0].score - (rows[1]?.score ?? 0),
        candidates: rows,
        quality,
        elapsed_ms: performance.now() - at,
      });
    } else throw Error("未知分析操作");
  } catch (e) {
    postMessage({ type: "error", error: String(e.message || e) });
  } finally {
    busy = false;
  }
};
