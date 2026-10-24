"use strict";
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let mode = "file", selectedFile = null, current = null, busy = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, {...options, headers: {"X-CSRF-Token": csrf, ...options.headers}}); }
  catch { throw new Error("Cannot reach the local app. Check that it is running, then reload this page. A submitted search may still be processing; check history before trying again."); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("The local app returned an unreadable response. Reload the page and check search history before retrying."); }
  if (data.quota) showQuota(data.quota);
  if (!response.ok) throw new Error(data.error?.message || "The request could not be completed.");
  return data;
}

function showMessage(message) {
  $("message").textContent = message;
  $("message").hidden = false;
  $("message").focus({preventScroll: true});
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll("form button, form input, form select, #refresh, #new-search, .history-item").forEach(el => { el.disabled = value; });
  $("clear-history").disabled = value || !$("history").querySelector("button");
  $("loading").hidden = !value;
  $("search-button").textContent = value ? "Searching…" : "Search with Google Lens ↗";
  $("search-form").setAttribute("aria-busy", String(value));
  $("image-url").disabled = value || mode !== "url";
  $("image-file").disabled = value || mode !== "file";
}

function showQuota(quota) {
  const month = quota.month;
  $("month-used").replaceChildren(document.createTextNode(String(month.used)), node("span", "", ` / ${month.limit} searches`));
  $("quota-progress").max = Math.max(1, month.limit);
  $("quota-progress").value = month.limit ? Math.min(month.used, month.limit) : 1;
  $("quota-detail").textContent = `Today: ${quota.day.used}/${quota.day.limit} · This hour: ${quota.hour.used}/${quota.hour.limit}`;
  $("provider-requests").textContent = `${month.requests} outbound SerpApi HTTP attempts this month (uploads and searches). Monthly reset: ${new Date(month.resets_at).toLocaleString()}.`;
}

function showHistory(history) {
  const list = $("history");
  list.replaceChildren();
  if (!history.length) list.append(node("p", "history-empty", "Your searches will appear here."));
  history.forEach(item => {
    const button = node("button", "history-item");
    button.type = "button";
    button.classList.toggle("active", current?.id === item.id);
    button.append(node("strong", "", item.source.label), node("small", "", `${item.count} results · ${new Date(item.created_at).toLocaleDateString()} · saved`));
    button.addEventListener("click", async () => {
      if (busy) return;
      $("message").hidden = true;
      try {
        const data = await api(`/api/history/${item.id}`);
        showSearch(data.search);
        await updateStatus();
      } catch (error) { showMessage(error.message); }
    });
    list.append(button);
  });
  $("clear-history").disabled = busy || history.length === 0;
}

async function updateStatus() {
  const data = await api("/api/status");
  $("key-notice").hidden = data.key_configured;
  showHistory(data.history);
}

function setMode(value) {
  mode = value;
  for (const name of ["file", "url"]) {
    $(`${name}-tab`).setAttribute("aria-selected", String(name === mode));
    $(`${name}-pane`).hidden = name !== mode;
  }
  $("image-url").required = mode === "url";
  $("image-url").disabled = busy || mode !== "url";
  $("image-file").disabled = busy || mode !== "file";
  $("search-cost").replaceChildren(document.createTextNode(mode === "file" ? "A new file search sends the image to SerpApi." : "A new URL search sends its public address to SerpApi."), document.createElement("br"), document.createTextNode(mode === "file" ? "1 local search · up to 2 provider calls · no automatic retries" : "1 local search · 1 provider call · no automatic retries"));
}

function chooseFile(file) {
  if (busy) return;
  $("message").hidden = true;
  if (!file) return;
  selectedFile = null;
  if (!file.size || file.size > 500000) {
    $("file-title").textContent = "Choose a JPEG, PNG or WebP image";
    $("file-help").textContent = "JPEG, PNG or WebP · up to 500 KB · still images only";
    $("image-file").value = "";
    showMessage("Choose an image no larger than 500,000 bytes. You can resize it using a local image editor.");
    return;
  }
  selectedFile = file;
  $("file-title").textContent = file.name;
  $("file-help").textContent = `${(file.size / 1000).toFixed(1)} KB · ready to search`;
}

function browserSafeLink(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function showSearch(search) {
  current = search;
  $("welcome").hidden = true;
  $("results-section").hidden = false;
  $("source-preview").src = `/api/preview/${search.id}?v=${encodeURIComponent(search.created_at)}`;
  $("source-label").textContent = search.source.label;
  $("cache-label").textContent = search.cached ? "Saved locally · 0 API calls" : "New search · saved locally";
  $("search-meta").textContent = `${search.parameters.type === "exact_matches" ? "Provider exact matches" : "Visual matches"} · ${search.parameters.hl}${search.parameters.country ? " · " + search.parameters.country.toUpperCase() : ""}`;
  $("image-hash").textContent = search.image_hash;
  $("saved-at").textContent = new Date(search.created_at).toLocaleString();
  $("export-csv").href = `/api/export/${search.id}.csv`;
  $("export-json").href = `/api/export/${search.id}.json`;
  $("result-warnings").textContent = search.result.warnings.join(" ");
  $("result-warnings").hidden = !search.result.warnings.length;
  $("domain-filter").replaceChildren(new Option("All results", ""));
  search.result.social_domains.forEach(domain => $("domain-filter").append(new Option(domain, domain)));
  $("domain-label").hidden = !search.result.social_domains.length;
  renderResults();
}

function renderResults() {
  if (!current) return;
  const grid = $("results-grid"), filter = $("domain-filter").value;
  grid.replaceChildren();
  const rows = current.result.results;
  let count = 0;
  rows.forEach((result, index) => {
    if (filter && result.domain !== filter) return;
    count++;
    const card = node("article", "result-card");
    const imageBox = node("div", "result-image");
    imageBox.append(node("span", "rank", `#${result.position || result.provider_order}`));
    const placeholder = node("span", "thumbnail-placeholder", result.thumbnail ? "Thumbnail not loaded" : "No thumbnail available");
    imageBox.append(placeholder);
    if (result.thumbnail && $("show-thumbnails").checked) {
      const image = node("img");
      image.alt = result.title;
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => { image.remove(); placeholder.hidden = false; placeholder.textContent = "Thumbnail unavailable"; });
      image.addEventListener("load", () => { placeholder.hidden = true; });
      image.src = `/api/thumbnail/${current.id}/${index}?v=${encodeURIComponent(current.created_at)}`;
      imageBox.append(image);
    }
    const body = node("div", "result-body");
    body.append(node("span", "result-domain", result.domain || "Source domain unavailable"), node("h3", "result-title", result.title));
    if (result.source) body.append(node("p", "result-source", result.source));
    if (current.parameters.type === "exact_matches") body.append(node("span", "match-label", result.match_label));
    const link = browserSafeLink(result.link);
    if (link) {
      const anchor = node("a", "", "Open original page ↗");
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer nofollow";
      body.append(anchor);
    } else body.append(node("span", "missing-link", "No safe original-page link available"));
    card.append(imageBox, body);
    grid.append(card);
  });
  $("no-matches").hidden = rows.length !== 0;
  $("results-count").textContent = `${filter ? `${count} of ` : ""}${rows.length} results · ${current.result.duplicates_removed} duplicate page links removed`;
}

async function performSearch(form) {
  if (busy) return;
  $("message").hidden = true;
  setBusy(true);
  try {
    const data = await api("/api/search", {method: "POST", body: form, headers: {"X-Request-ID": crypto.randomUUID()}});
    showSearch(data.search);
  } catch (error) { showMessage(error.message); }
  finally {
    setBusy(false);
    try { await updateStatus(); } catch (error) { showMessage(error.message); }
  }
}

$("file-tab").addEventListener("click", () => setMode("file"));
$("url-tab").addEventListener("click", () => setMode("url"));
$("image-file").addEventListener("change", event => chooseFile(event.target.files[0]));
$("dropzone").addEventListener("dragover", event => { event.preventDefault(); if (!busy) $("dropzone").classList.add("dragging"); });
$("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("dragging"));
$("dropzone").addEventListener("drop", event => { event.preventDefault(); $("dropzone").classList.remove("dragging"); if (!busy) chooseFile(event.dataTransfer.files[0]); });
$("domain-filter").addEventListener("change", renderResults);
$("show-thumbnails").addEventListener("change", renderResults);
$("search-form").addEventListener("submit", event => {
  event.preventDefault();
  if (busy) return;
  const form = new FormData();
  if (mode === "file") {
    if (!selectedFile) { showMessage("Choose an image file to search."); return; }
    form.append("image", selectedFile);
  } else form.append("url", $("image-url").value.trim());
  form.append("type", $("search-type").value);
  form.append("hl", $("language").value.trim().toLowerCase());
  form.append("country", $("country").value.trim().toLowerCase());
  performSearch(form);
});
$("refresh").addEventListener("click", () => {
  if (!current || busy) return;
  const form = new FormData();
  form.append("refresh_id", current.id);
  performSearch(form);
});
$("new-search").addEventListener("click", () => {
  if (busy) return;
  current = null;
  selectedFile = null;
  $("search-form").reset();
  $("file-title").textContent = "Drop an image here, or browse files";
  $("file-help").textContent = "JPEG, PNG or WebP · up to 500 KB · still images only";
  $("results-section").hidden = true;
  $("results-grid").replaceChildren();
  $("welcome").hidden = false;
  $("message").hidden = true;
  document.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
  setMode("file");
  $("file-tab").focus();
});
$("clear-history").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    await api("/api/history", {method: "DELETE"});
    current = null;
    $("results-section").hidden = true;
    $("results-grid").replaceChildren();
    $("welcome").hidden = false;
    $("message").hidden = true;
    showHistory([]);
  } catch (error) { showMessage(error.message); }
  finally { setBusy(false); }
});
setMode("file");
updateStatus().catch(error => showMessage(error.message));
