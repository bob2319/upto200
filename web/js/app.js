const API_BASE = "/api";

const ACCEPTED_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".heic",
  ".heif",
]);

const HEIC_EXTENSIONS = new Set([".heic", ".heif"]);
const MAX_FILE_BYTES = 15 * 1024 * 1024;
const REDUCED_STATUSES = new Set(["optimized", "target_not_reached"]);
const POLL_INTERVAL_MS = 350;

const state = {
  files: [],
  objectUrls: new Map(),
  originalPreviewUrls: new Map(),
  zipUrl: null,
  successCount: 0,
  isProcessing: false,
  pollTimer: null,
};

const els = {
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  queue: document.getElementById("queue"),
  queueList: document.getElementById("queue-list"),
  queueCount: document.getElementById("queue-count"),
  clearAll: document.getElementById("clear-all"),
  optimizeBtn: document.getElementById("optimize-btn"),
  settingsError: document.getElementById("settings-error"),
  uploadProgress: document.getElementById("upload-progress"),
  uploadProgressBar: document.getElementById("upload-progress-bar"),
  processing: document.getElementById("processing"),
  processingTitle: document.getElementById("processing-title"),
  processingDetail: document.getElementById("processing-detail"),
  processingPercent: document.getElementById("processing-percent"),
  processingTrack: document.getElementById("processing-track"),
  processingBar: document.getElementById("processing-bar"),
  processingFiles: document.getElementById("processing-files"),
  results: document.getElementById("results"),
  resultsList: document.getElementById("results-list"),
  resultsSummary: document.getElementById("results-summary"),
  downloadZip: document.getElementById("download-zip"),
  zipError: document.getElementById("zip-error"),
  processMore: document.getElementById("process-more"),
  targetSize: document.getElementById("target-size"),
  maxWidth: document.getElementById("max-width"),
  outputFormat: document.getElementById("output-format"),
  startQuality: document.getElementById("start-quality"),
  minQuality: document.getElementById("min-quality"),
  transparencyNote: document.getElementById("transparency-note"),
  queueItemTemplate: document.getElementById("queue-item-template"),
  resultItemTemplate: document.getElementById("result-item-template"),
  header: document.querySelector(".header"),
};

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function extensionOf(name) {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function showError(message) {
  els.settingsError.hidden = !message;
  els.settingsError.textContent = message || "";
}

function revokeAllUrls() {
  for (const url of state.objectUrls.values()) URL.revokeObjectURL(url);
  for (const url of state.originalPreviewUrls.values()) URL.revokeObjectURL(url);
  state.objectUrls.clear();
  state.originalPreviewUrls.clear();
}

function headerOffset() {
  return els.header ? els.header.getBoundingClientRect().height + 12 : 12;
}

function scrollToElement(element) {
  if (!element) return;
  const top = element.getBoundingClientRect().top + window.scrollY - headerOffset();
  window.scrollTo({
    top: Math.max(0, top),
    behavior: "smooth",
  });
}

function scrollToProcessing() {
  scrollToElement(els.processing);
}

function scrollToResults() {
  scrollToElement(els.results);
}

function readImageDimensions(previewUrl) {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.onerror = () => {
      resolve({ width: null, height: null });
    };
    image.src = previewUrl;
  });
}

async function fetchServerPreview(file) {
  const form = new FormData();
  form.append("file", file, file.name);
  const url = `${API_BASE}/preview`;
  console.debug("[upto200:preview] POST", url, file.name, file.type, file.size);
  const response = await fetch(url, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    console.error("[upto200:preview] failed", response.status, url, file.name);
    throw new Error(`Preview conversion failed (${response.status})`);
  }
  const blob = await response.blob();
  console.debug("[upto200:preview] ok", file.name, blob.type, blob.size);
  return URL.createObjectURL(blob);
}

async function createOriginalPreview(file) {
  const ext = extensionOf(file.name);
  const objectUrl = URL.createObjectURL(file);

  if (HEIC_EXTENSIONS.has(ext)) {
    URL.revokeObjectURL(objectUrl);
    try {
      const previewUrl = await fetchServerPreview(file);
      const dims = await readImageDimensions(previewUrl);
      return { previewUrl, width: dims.width, height: dims.height, ok: Boolean(dims.width) };
    } catch (_error) {
      return { previewUrl: null, width: null, height: null, ok: false };
    }
  }

  const dims = await readImageDimensions(objectUrl);
  if (dims.width && dims.height) {
    return { previewUrl: objectUrl, width: dims.width, height: dims.height, ok: true };
  }

  URL.revokeObjectURL(objectUrl);
  try {
    const previewUrl = await fetchServerPreview(file);
    const convertedDims = await readImageDimensions(previewUrl);
    return {
      previewUrl,
      width: convertedDims.width,
      height: convertedDims.height,
      ok: Boolean(convertedDims.width),
    };
  } catch (_error) {
    return { previewUrl: null, width: null, height: null, ok: false };
  }
}

function showPlaceholderOnly(img, placeholder) {
  if (img) {
    img.onload = null;
    img.onerror = null;
    img.removeAttribute("src");
    img.removeAttribute("alt");
    img.alt = "";
    img.hidden = true;
    img.style.display = "none";
  }
  if (placeholder) {
    placeholder.hidden = false;
    placeholder.style.display = "";
  }
}

function applyPreview(img, placeholder, previewUrl, altText) {
  if (!img) return;
  if (previewUrl) {
    if (placeholder) {
      placeholder.hidden = true;
      placeholder.style.display = "none";
    }
    img.hidden = false;
    img.style.display = "";
    img.alt = altText;
    img.onload = () => {
      img.hidden = false;
      img.style.display = "";
      if (placeholder) {
        placeholder.hidden = true;
        placeholder.style.display = "none";
      }
    };
    img.onerror = () => {
      showPlaceholderOnly(img, placeholder);
    };
    img.src = previewUrl;
    return;
  }
  showPlaceholderOnly(img, placeholder);
}

async function addFiles(fileList) {
  if (state.isProcessing) return;
  showError("");
  const incoming = Array.from(fileList || []);
  for (const file of incoming) {
    const ext = extensionOf(file.name);
    if (!ACCEPTED_EXTENSIONS.has(ext)) {
      showError(`Skipped unsupported file: ${file.name}`);
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      showError(`Skipped oversized file (max 15 MB): ${file.name}`);
      continue;
    }
    const already = state.files.some(
      (item) =>
        item.file.name === file.name &&
        item.file.size === file.size &&
        item.file.lastModified === file.lastModified
    );
    if (already) continue;

    const id = crypto.randomUUID();
    const preview = await createOriginalPreview(file);
    if (preview.previewUrl) {
      state.originalPreviewUrls.set(id, preview.previewUrl);
    }
    state.files.push({
      id,
      file,
      width: preview.width,
      height: preview.height,
      previewOk: preview.ok,
    });
  }
  renderQueue();
}

function removeFile(id) {
  if (state.isProcessing) return;
  state.files = state.files.filter((item) => item.id !== id);
  const preview = state.originalPreviewUrls.get(id);
  if (preview) {
    URL.revokeObjectURL(preview);
    state.originalPreviewUrls.delete(id);
  }
  renderQueue();
}

function clearFiles() {
  if (state.isProcessing) return;
  for (const id of state.originalPreviewUrls.keys()) {
    URL.revokeObjectURL(state.originalPreviewUrls.get(id));
  }
  state.originalPreviewUrls.clear();
  state.files = [];
  renderQueue();
}

function renderQueue() {
  els.queue.hidden = state.files.length === 0;
  els.queueCount.textContent = String(state.files.length);
  els.optimizeBtn.disabled = state.isProcessing || state.files.length === 0;
  els.queueList.innerHTML = "";

  for (const item of state.files) {
    const node = els.queueItemTemplate.content.cloneNode(true);
    const root = node.querySelector(".queue-item");
    const preview = node.querySelector(".queue-item__preview");
    const placeholder = node.querySelector(".queue-item__placeholder");
    const name = node.querySelector(".queue-item__name");
    const info = node.querySelector(".queue-item__info");
    const removeBtn = node.querySelector(".queue-item__remove");

    name.textContent = item.file.name;
    const dimText =
      item.width && item.height ? `${item.width}×${item.height}px · ` : "";
    info.textContent = `${dimText}${formatBytes(item.file.size)}`;

    applyPreview(
      preview,
      placeholder,
      state.originalPreviewUrls.get(item.id) || null,
      `Preview of ${item.file.name}`
    );

    removeBtn.disabled = state.isProcessing;
    removeBtn.addEventListener("click", () => removeFile(item.id));
    els.queueList.appendChild(root);
  }
}

function getSettings() {
  const targetSize = Number(els.targetSize.value);
  const maxWidth = Number(els.maxWidth.value);
  const startQuality = Number(els.startQuality.value);
  const minQuality = Number(els.minQuality.value);
  const outputFormat = els.outputFormat.value;

  if (!Number.isFinite(targetSize) || targetSize < 1 || targetSize > 10000) {
    throw new Error("Target size must be between 1 and 10000 KB.");
  }
  if (!Number.isFinite(maxWidth) || maxWidth < 16 || maxWidth > 10000) {
    throw new Error("Maximum width must be between 16 and 10000 px.");
  }
  if (
    !Number.isFinite(minQuality) ||
    !Number.isFinite(startQuality) ||
    minQuality < 1 ||
    startQuality > 100 ||
    minQuality > startQuality
  ) {
    throw new Error("Require 1 ≤ minimum quality ≤ starting quality ≤ 100.");
  }
  if (!["webp", "jpeg", "png"].includes(outputFormat)) {
    throw new Error("Unsupported output format.");
  }

  return { targetSize, maxWidth, startQuality, minQuality, outputFormat };
}

function setProgress(percent) {
  els.uploadProgress.hidden = false;
  els.uploadProgressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function hideProgress() {
  els.uploadProgress.hidden = true;
  els.uploadProgressBar.style.width = "0%";
}

function statusLabel(status) {
  switch (status) {
    case "processing":
      return "Processing...";
    case "optimized":
      return "Optimized";
    case "target_not_reached":
      return "Target not reached";
    case "not_smaller":
      return "No size reduction";
    case "error":
      return "Error";
    case "pending":
      return "Waiting...";
    default:
      return status || "Waiting...";
  }
}

function setProcessingProgress(percent, detail, currentFilename) {
  const safe = Math.max(0, Math.min(100, Number(percent) || 0));
  els.processingPercent.textContent = `${Math.round(safe)}%`;
  els.processingBar.style.width = `${safe}%`;
  if (els.processingTrack) {
    els.processingTrack.setAttribute("aria-valuenow", String(Math.round(safe)));
  }
  const parts = [];
  if (detail) parts.push(detail);
  if (currentFilename) parts.push(currentFilename);
  els.processingDetail.textContent = parts.join(" · ");
  els.processingDetail.hidden = parts.length === 0;
}

function renderProcessingFiles(results) {
  els.processingFiles.innerHTML = "";
  for (const result of results || []) {
    const li = document.createElement("li");
    const status = result.status || "pending";
    li.className = `processing__file processing__file--${
      status === "optimized" || status === "target_not_reached" || status === "not_smaller"
        ? "done"
        : status
    }`;
    const name = document.createElement("span");
    name.className = "processing__file-name";
    name.textContent = result.original_filename;
    const label = document.createElement("span");
    label.className = "processing__file-status";
    label.textContent = statusLabel(status);
    li.append(name, label);
    els.processingFiles.appendChild(li);
  }
}

function showProcessing(title, detail) {
  els.processing.hidden = false;
  els.processing.setAttribute("aria-busy", "true");
  els.processingTitle.textContent = title;
  setProcessingProgress(0, detail || "", "");
  renderProcessingFiles(
    state.files.map((item) => ({
      original_filename: item.file.name,
      status: "pending",
    }))
  );
}

function hideProcessing() {
  els.processing.hidden = true;
  els.processing.setAttribute("aria-busy", "false");
  els.processingTitle.textContent = "Optimizing images…";
  setProcessingProgress(0, "", "");
  els.processingFiles.innerHTML = "";
}

function setProcessingLock(locked) {
  state.isProcessing = locked;
  els.optimizeBtn.disabled = locked || state.files.length === 0;
  els.clearAll.disabled = locked;
  els.fileInput.disabled = locked;
  els.dropzone.classList.toggle("dropzone--locked", locked);
  if (locked) {
    els.dropzone.setAttribute("aria-disabled", "true");
  } else {
    els.dropzone.removeAttribute("aria-disabled");
  }
  renderQueue();
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function optimizeRequest(settings, onUploadProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const item of state.files) {
      form.append("files", item.file, item.file.name);
    }
    form.append("target_size_kb", String(settings.targetSize));
    form.append("max_width", String(settings.maxWidth));
    form.append("output_format", settings.outputFormat);
    form.append("start_quality", String(settings.startQuality));
    form.append("min_quality", String(settings.minQuality));
    form.append("step_quality", "5");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/optimize`);
    xhr.responseType = "json";

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = (event.loaded / event.total) * 100;
      setProgress(percent);
      if (typeof onUploadProgress === "function") {
        onUploadProgress(percent);
      }
    };

    xhr.upload.onload = () => {
      hideProgress();
      if (typeof onUploadProgress === "function") {
        onUploadProgress(100);
      }
    };

    xhr.onload = () => {
      hideProgress();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
        return;
      }
      const detail =
        (xhr.response && xhr.response.detail) ||
        xhr.statusText ||
        "Optimization request failed.";
      reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
    };

    xhr.onerror = () => {
      hideProgress();
      reject(new Error("Network error while uploading images."));
    };

    setProgress(0);
    xhr.send(form);
  });
}

async function fetchJob(jobId) {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`);
  if (!response.ok) {
    let detail = "Failed to fetch job progress.";
    try {
      const payload = await response.json();
      if (payload && payload.detail) detail = String(payload.detail);
    } catch (_err) {
      // keep default
    }
    throw new Error(detail);
  }
  return response.json();
}

function pollUntilComplete(jobId, onUpdate) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const payload = await fetchJob(jobId);
        console.debug("[upto200:poll]", {
          jobId,
          status: payload.status,
          progress_percent: payload.progress_percent,
          processed_count: payload.processed_count,
          total_count: payload.total_count,
          current_filename: payload.current_filename,
          file_statuses: (payload.results || []).map((item) => item.status),
        });
        if (typeof onUpdate === "function") onUpdate(payload);
        if (payload.status === "completed") {
          state.pollTimer = null;
          resolve(payload);
          return;
        }
        state.pollTimer = window.setTimeout(tick, POLL_INTERVAL_MS);
      } catch (error) {
        state.pollTimer = null;
        console.error("[upto200:poll] failed", error);
        reject(error);
      }
    };
    tick();
  });
}

function showZipError(message) {
  els.zipError.hidden = !message;
  els.zipError.textContent = message || "";
}

function updateZipButton(successCount, zipUrl) {
  state.successCount = successCount;
  state.zipUrl = zipUrl || null;

  const label =
    successCount === 1
      ? "Download all (.ZIP) · 1 image"
      : `Download all (.ZIP) · ${successCount} images`;
  els.downloadZip.textContent = label;
  els.downloadZip.disabled = successCount === 0 || !zipUrl;
  els.downloadZip.setAttribute(
    "aria-disabled",
    els.downloadZip.disabled ? "true" : "false"
  );
}

function isReducedResult(result) {
  return Boolean(result.size_reduced) || REDUCED_STATUSES.has(result.status);
}

function applyResultBadge(badge, result) {
  badge.classList.remove(
    "result-card__badge--ok",
    "result-card__badge--warn",
    "result-card__badge--error",
    "result-card__badge--neutral"
  );

  if (result.status === "error") {
    badge.textContent = "Failed";
    badge.classList.add("result-card__badge--error");
    return;
  }
  if (result.status === "optimized") {
    badge.textContent = "Target reached";
    badge.classList.add("result-card__badge--ok");
    return;
  }
  if (result.status === "target_not_reached") {
    badge.textContent = "Target not reached";
    badge.classList.add("result-card__badge--warn");
    return;
  }
  badge.textContent = "No size reduction";
  badge.classList.add("result-card__badge--neutral");
}

function renderResults(payload) {
  els.results.hidden = false;
  els.resultsList.innerHTML = "";
  showZipError("");

  const successCount =
    typeof payload.success_count === "number"
      ? payload.success_count
      : payload.results.filter((item) => isReducedResult(item)).length;
  const totalCount = payload.results.length;
  const errorCount =
    typeof payload.error_count === "number"
      ? payload.error_count
      : payload.results.filter((item) => item.status === "error").length;
  const notSmallerCount =
    typeof payload.not_smaller_count === "number"
      ? payload.not_smaller_count
      : payload.results.filter((item) => item.status === "not_smaller").length;

  const parts = [];
  if (successCount === 0) {
    parts.push(`0 of ${totalCount} images optimized`);
  } else if (errorCount === 0 && notSmallerCount === 0) {
    parts.push(
      successCount === 1
        ? "1 image optimized successfully"
        : `${successCount} images optimized successfully`
    );
  } else {
    parts.push(`${successCount} of ${totalCount} images optimized successfully`);
  }
  if (errorCount > 0) parts.push(`${errorCount} error${errorCount === 1 ? "" : "s"}`);
  if (notSmallerCount > 0) {
    parts.push(
      `${notSmallerCount} without size reduction`
    );
  }
  els.resultsSummary.textContent = parts.join(" · ");

  updateZipButton(successCount, payload.zip_url);

  payload.results.forEach((result, index) => {
    const node = els.resultItemTemplate.content.cloneNode(true);
    const card = node.querySelector(".result-card");
    const name = node.querySelector(".result-card__name");
    const badge = node.querySelector(".result-card__badge");
    const originalImg = node.querySelector(".result-card__image--original");
    const originalPlaceholder = node.querySelector(
      ".result-card__placeholder--original"
    );
    const optimizedImg = node.querySelector(".result-card__image--optimized");
    const download = node.querySelector(".result-card__download");
    const error = node.querySelector(".result-card__error");

    name.textContent = result.original_filename;
    applyResultBadge(badge, result);

    const queued = state.files[index];
    const originalUrl = queued
      ? state.originalPreviewUrls.get(queued.id)
      : null;
    applyPreview(
      originalImg,
      originalPlaceholder,
      originalUrl || null,
      `Original preview of ${result.original_filename}`
    );

    if (result.status === "error") {
      card.querySelector(".result-card__compare").hidden = true;
      card.querySelector(".result-card__stats").hidden = true;
      download.hidden = true;
      error.hidden = false;
      error.textContent = result.error || "Optimization failed.";
      els.resultsList.appendChild(card);
      return;
    }

    if (result.download_url) {
      optimizedImg.hidden = false;
      optimizedImg.src = result.download_url;
      optimizedImg.alt = `Optimized preview of ${result.original_filename}`;
      download.hidden = false;
      download.href = result.download_url;
      download.download =
        result.original_filename.replace(/\.[^.]+$/, "") +
        (result.output_format === "jpeg" ? ".jpg" : `.${result.output_format}`);
    } else {
      optimizedImg.hidden = true;
      optimizedImg.removeAttribute("src");
      download.hidden = true;
    }

    node.querySelector(".result-card__original-size").textContent = formatBytes(
      result.original_size_bytes
    );
    node.querySelector(".result-card__optimized-size").textContent = formatBytes(
      result.optimized_size_bytes
    );

    const reduced = isReducedResult(result);
    node.querySelector(".result-card__reduction").textContent = reduced
      ? `${result.compression_percent}%`
      : "Not reduced";
    node.querySelector(".result-card__dimensions").textContent =
      `${result.output_width}×${result.output_height}px`;
    node.querySelector(".result-card__quality").textContent = String(result.quality);
    node.querySelector(".result-card__target").textContent = result.target_achieved
      ? "Yes"
      : "No";

    els.resultsList.appendChild(card);
  });

  return successCount;
}

async function onDownloadZip() {
  showZipError("");
  if (!state.zipUrl || state.successCount === 0) {
    showZipError("No successfully optimized images are available for ZIP download.");
    return;
  }

  els.downloadZip.disabled = true;
  const previousLabel = els.downloadZip.textContent;
  els.downloadZip.textContent = "Preparing ZIP…";

  try {
    const response = await fetch(state.zipUrl);
    if (!response.ok) {
      let detail = "ZIP download failed. Please try again.";
      try {
        const payload = await response.json();
        if (payload && payload.detail) detail = String(payload.detail);
      } catch (_err) {
        // Keep default message when body is not JSON.
      }
      throw new Error(detail);
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    anchor.href = objectUrl;
    anchor.download = `optimized-${stamp}.zip`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    showZipError(error.message || "ZIP download failed. Please try again.");
  } finally {
    els.downloadZip.textContent = previousLabel;
    updateZipButton(state.successCount, state.zipUrl);
  }
}

function updateProcessingFromPayload(payload) {
  const total = payload.total_count || payload.results.length || state.files.length;
  const processed = payload.processed_count || 0;
  const detail =
    total > 0 ? `File ${Math.min(processed + (payload.current_filename ? 1 : 0), total)} of ${total}` : "";
  setProcessingProgress(
    payload.progress_percent,
    detail,
    payload.current_filename || ""
  );
  renderProcessingFiles(payload.results);
}

async function onOptimize() {
  if (state.isProcessing) return;

  showError("");
  let settings;
  try {
    settings = getSettings();
  } catch (error) {
    showError(error.message);
    return;
  }

  const totalFiles = state.files.length;
  setProcessingLock(true);
  els.results.hidden = true;
  showZipError("");
  updateZipButton(0, null);
  stopPolling();

  showProcessing(
    "Optimizing images…",
    totalFiles > 1 ? `${totalFiles} files in queue` : "1 file in queue"
  );
  // Scroll immediately — do not wait for the server.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => scrollToProcessing());
  });

  try {
    const started = await optimizeRequest(settings, (percent) => {
      if (percent < 100) {
        setProcessingProgress(
          0,
          totalFiles > 1 ? "Uploading files…" : "Uploading file…",
          ""
        );
      } else {
        setProcessingProgress(0, "Upload complete · starting processing…", "");
      }
    });

    updateProcessingFromPayload(started);

    const payload =
      started.status === "completed"
        ? started
        : await pollUntilComplete(started.job_id, updateProcessingFromPayload);

    setProcessingProgress(100, "Processing complete", "");
    renderProcessingFiles(payload.results);

    const renderedSuccess = renderResults(payload);
    hideProcessing();

    if (renderedSuccess > 0) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => scrollToResults());
      });
    }
  } catch (error) {
    stopPolling();
    hideProcessing();
    showError(error.message || "Optimization failed. Adjust settings and try again.");
  } finally {
    setProcessingLock(false);
  }
}

function updateTransparencyNote() {
  const format = els.outputFormat.value;
  if (format === "jpeg") {
    els.transparencyNote.textContent =
      "JPEG output flattens transparency onto a white background.";
  } else {
    els.transparencyNote.textContent =
      "Transparency is preserved for WebP and PNG output when the source has an alpha channel.";
  }
}

function bindEvents() {
  els.dropzone.addEventListener("click", () => {
    if (state.isProcessing) return;
    els.fileInput.click();
  });
  els.dropzone.addEventListener("keydown", (event) => {
    if (state.isProcessing) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      els.fileInput.click();
    }
  });

  els.fileInput.addEventListener("change", async () => {
    if (state.isProcessing) return;
    await addFiles(els.fileInput.files);
    els.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      if (state.isProcessing) return;
      els.dropzone.classList.add("dropzone--active");
    });
  });

  ["dragleave", "drop"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("dropzone--active");
    });
  });

  els.dropzone.addEventListener("drop", async (event) => {
    if (state.isProcessing) return;
    await addFiles(event.dataTransfer.files);
  });

  els.clearAll.addEventListener("click", clearFiles);
  els.optimizeBtn.addEventListener("click", onOptimize);
  els.downloadZip.addEventListener("click", onDownloadZip);
  els.outputFormat.addEventListener("change", updateTransparencyNote);
  els.processMore.addEventListener("click", () => {
    if (state.isProcessing) return;
    els.results.hidden = true;
    hideProcessing();
    showZipError("");
    updateZipButton(0, null);
    clearFiles();
    window.location.hash = "#optimizer";
    els.dropzone.focus();
  });
}

bindEvents();
updateTransparencyNote();

window.addEventListener("beforeunload", () => {
  stopPolling();
  revokeAllUrls();
});
