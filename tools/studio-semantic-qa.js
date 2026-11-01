(async () => {
  if (window.studioDebug.mode !== "simulation") throw Error("QA只允许演练");
  window.semanticQA = { started: Date.now(), results: [] };
  const w = new Worker("/static/semantic-worker.js", { type: "module" });
  window.qaWorker = w;
  let next;
  const once = () =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("模型/推理超时")), 120000);
      next = (d) => {
        clearTimeout(timer);
        d.type === "error" ? reject(Error(d.error)) : resolve(d);
      };
    });
  w.onmessage = ({ data }) => {
    if (data.type !== "progress") next?.(data);
  };
  let p = once();
  w.postMessage({
    op: "load",
    model_sha:
      "10921310ddef06557ec1598d1260470a0a4db53f70ffe0deb60b946dcad6d27a",
  });
  await p;
  const samples = await fetch("/assets/validation/samples.json").then((r) =>
    r.json(),
  );
  for (const row of samples.samples) {
    const blob = await fetch("/assets/validation/" + row.file).then((r) =>
        r.blob(),
      ),
      bitmap = await createImageBitmap(blob),
      c = document.createElement("canvas");
    c.width = c.height = 224;
    const ctx = c.getContext("2d");
    ctx.drawImage(bitmap, 0, 0, 224, 224);
    bitmap.close();
    const pixels = ctx.getImageData(0, 0, 224, 224).data;
    p = once();
    w.postMessage(
      {
        op: "classify",
        frame_id: row.file,
        pixels: pixels.buffer,
        width: 224,
        height: 224,
      },
      [pixels.buffer],
    );
    const result = await p;
    semanticQA.results.push({ expected: row.label, ...result });
  }
  w.terminate();
  semanticQA.finished = Date.now();
  semanticQA.scope =
    "six independent public images, one per coarse class; smoke only, not robot-camera accuracy";
  return semanticQA;
})();
