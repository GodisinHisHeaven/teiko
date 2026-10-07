"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const LABELS = {b_cell: "B cells", cd8_t_cell: "CD8 T cells", cd4_t_cell: "CD4 T cells", nk_cell: "NK cells", monocyte: "Monocytes"};
const COLORS = ["#346ce7", "#409d99", "#8b7dc7", "#d7a15e", "#8197b8"];
const RESPONSE_COLORS = {yes: "#346ce7", no: "#e7a16b"};
const NUM = new Intl.NumberFormat("en-US");
const config = {responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"], toImageButtonOptions: {format: "svg", filename: "immune-cell-analysis"}};
let data;
let page = 0;
let filtered = [];
const PAGE_SAMPLES = 5;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const number = (value, digits = 2) => value == null ? "n/a" : Number(value).toFixed(digits);
const pvalue = (value) => value == null ? "n/a" : value < 0.001 ? value.toExponential(2) : value.toFixed(4);
const frequency = (sample, population) => sample.total_count ? 100 * sample[population] / sample.total_count : null;
const cohortSamples = () => data.samples.filter((s) => s.condition === "melanoma" && s.treatment === "miraclib" && s.sample_type === "PBMC" && ["yes", "no"].includes(s.response));

function layout(extra = {}) {
  return {paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: {family: '"Helvetica Neue", Helvetica, Arial, sans-serif', size: 12, color: "#6c7b90"}, margin: {l: 60, r: 25, t: 25, b: 50}, hoverlabel: {bgcolor: "#fff", bordercolor: "#e7ecf3", font: {size: 12, color: "#17273f"}}, ...extra};
}

function metric(label, value, note, blue = false) {
  return `<div class="metric"><div class="metric-label">${escapeHTML(label)}</div><div class="metric-value${blue ? " blue-text" : ""}">${escapeHTML(value)}</div><div class="metric-note">${escapeHTML(note)}</div></div>`;
}

function showView() {
  const key = ["overview", "response", "baseline"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "response";
  $$(".view").forEach((section) => section.hidden = section.id !== `view-${key}`);
  $$("nav a").forEach((link) => {
    const active = link.dataset.view === key;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("#breadcrumb").textContent = {overview: "Sample overview", response: "Treatment response", baseline: "Baseline cohort"}[key];
  if (key === "response") renderResponse();
  if (key === "overview") renderOverview();
  if (key === "baseline") renderBaseline();
  window.scrollTo(0, 0);
}

function renderResponse() {
  const baseline = $("#response-window").value === "baseline";
  const individual = $("#response-unit").value === "sample";
  const statistics = baseline ? data.response.baseline_statistics : data.response.statistics;
  const observations = baseline ? data.response.baseline_subjects : data.response.subjects;
  const samples = cohortSamples().filter((s) => !baseline || s.time_from_treatment_start === 0);
  const significant = statistics.filter((s) => s.significant);
  const nYes = new Set(observations.filter((s) => s.response === "yes").map((s) => s.subject)).size;
  const nNo = new Set(observations.filter((s) => s.response === "no").map((s) => s.subject)).size;
  $("#response-metrics").innerHTML = metric("Eligible samples", NUM.format(samples.length), baseline ? "Baseline · day 0" : "All visits · days 0, 7 and 14")
    + metric("Responders", NUM.format(nYes), "Distinct analyzed subjects")
    + metric("Non-responders", NUM.format(nNo), "Distinct analyzed subjects")
    + metric("Significant populations", `${significant.length} / 5`, "After multiple-testing correction", true);
  $("#chart-subtitle").textContent = individual ? "Each observation is one biological sample. Statistical results below use subject averages." : "Each observation is one subject’s mean relative frequency in the selected time window.";
  const traces = ["yes", "no"].map((response) => {
    const points = individual
      ? samples.filter((s) => s.response === response && s.total_count > 0).flatMap((s) => data.populations.map((population) => ({population, percentage: frequency(s, population), id: s.sample})))
      : observations.filter((s) => s.response === response).map((s) => ({...s, id: s.subject}));
    return {type: "box", name: response === "yes" ? "Responders" : "Non-responders", x: points.map((p) => LABELS[p.population]), y: points.map((p) => p.percentage), customdata: points.map((p) => p.id), legendgroup: response,
      marker: {color: RESPONSE_COLORS[response], size: 3, opacity: 0.45}, line: {color: RESPONSE_COLORS[response], width: 1.6}, fillcolor: response === "yes" ? "rgba(52,108,231,0.13)" : "rgba(231,161,107,0.18)",
      boxpoints: $("#show-points").checked ? "all" : "outliers", jitter: 0.35, pointpos: 0, quartilemethod: "linear", hovertemplate: "%{customdata}<br>%{x}: %{y:.2f}%<extra>%{fullData.name}</extra>"};
  });
  Plotly.react("response-chart", traces, layout({boxmode: "group", boxgap: 0.45, boxgroupgap: 0.12, showlegend: false, xaxis: {categoryorder: "array", categoryarray: data.populations.map((p) => LABELS[p]), showgrid: false, zeroline: false, tickfont: {size: 11}}, yaxis: {title: {text: "Relative frequency (%)", font: {size: 10}, standoff: 12}, ticksuffix: "%", gridcolor: "#eef2f7", zeroline: false, rangemode: "tozero"}}), config);
  const excluded = samples.filter((s) => s.total_count === 0).length;
  $("#chart-footnote").textContent = `Boxes: median and interquartile range. Whiskers: 1.5 × IQR. ${individual && !baseline ? "Repeated visits are shown for description only. " : ""}${excluded ? `${excluded} zero-total samples excluded from frequencies. ` : ""}Percentages use the sum of the five measured cell populations.`;
  const finding = significant.length
    ? `<strong>${significant.map((s) => LABELS[s.population]).join(" and ")} differ by response</strong> after correction for five tests. ${baseline ? "These baseline associations need independent validation before use for prediction." : "This comparison includes post-treatment visits. Use the baseline window to examine pretreatment associations."}`
    : `<strong>No population meets the adjusted 0.05 threshold</strong> in this time window. This does not establish that the groups are equivalent.`;
  $("#finding").innerHTML = `<span class="finding-icon">Finding</span><div>${finding}</div>`;
  $("#stats-table tbody").innerHTML = statistics.map((s, i) => `<tr><td><span class="population-dot" style="background:${COLORS[i]}"></span>${LABELS[s.population]}</td><td>${number(s.mean_responders)}</td><td>${number(s.mean_nonresponders)}</td><td>${s.mean_difference_pp > 0 ? "+" : ""}${number(s.mean_difference_pp)} <span style="color:#6c7b90">(${number(s.ci_low)}, ${number(s.ci_high)})</span></td><td>${pvalue(s.p_value)}</td><td>${pvalue(s.p_holm)}</td><td>${s.p_holm == null ? '<span class="ns-tag">Insufficient data</span>' : s.significant ? '<span class="sig-tag">Significant</span>' : '<span class="ns-tag">Not significant</span>'}</td></tr>`).join("");
  $("#stats-download").href = `downloads/${baseline ? "baseline" : "response"}_statistics.csv`;
}

function setupFilters() {
  for (const [id, key] of [["project", "project"], ["condition", "condition"], ["type", "sample_type"], ["treatment", "treatment"]]) {
    const select = $(`#filter-${id}`);
    [...new Set(data.samples.map((s) => s[key]))].sort().forEach((value) => select.add(new Option(value, value)));
    select.addEventListener("change", () => {page = 0; renderOverview();});
  }
  $("#sample-search").addEventListener("input", () => {page = 0; renderOverview();});
  $("#reset-filters").addEventListener("click", () => { $$(".filters select, .filters input").forEach((el) => el.value = ""); page = 0; renderOverview(); });
  $("#prev-page").addEventListener("click", () => {page--; renderFrequencyTable();});
  $("#next-page").addEventListener("click", () => {page++; renderFrequencyTable();});
  $("#selected-sample").addEventListener("change", renderSelectedSample);
  $("#download-filtered").addEventListener("click", () => {
    const lines = [["sample", "total_count", "population", "count", "percentage"], ...filtered.flatMap((s) => data.populations.map((p) => [s.sample, s.total_count, p, s[p], frequency(s, p) ?? ""]))];
    const csv = lines.map((row) => row.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = "filtered_sample_frequencies.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

function renderOverview() {
  const search = $("#sample-search").value.toLowerCase().trim();
  filtered = data.samples.filter((s) => [["project", "project"], ["condition", "condition"], ["type", "sample_type"], ["treatment", "treatment"]].every(([id, key]) => !$(`#filter-${id}`).value || s[key] === $(`#filter-${id}`).value)
    && (!search || s.sample.toLowerCase().includes(search) || s.subject.toLowerCase().includes(search)));
  $("#overview-metrics").innerHTML = metric("Samples", NUM.format(filtered.length), `Of ${NUM.format(data.overview.samples)} in the dataset`)
    + metric("Subjects", NUM.format(new Set(filtered.map((s) => s.subject)).size), "Distinct subjects in selection")
    + metric("Projects", new Set(filtered.map((s) => s.project)).size, "Projects in selection")
    + metric("Frequency rows", NUM.format(filtered.length * 5), "One row per sample and population", true);
  const available = filtered.filter((s) => s.total_count > 0);
  const means = data.populations.map((p) => available.length ? available.reduce((total, s) => total + frequency(s, p), 0) / available.length : 0);
  if (available.length) {
    Plotly.react("composition-chart", [{type: "pie", labels: data.populations.map((p) => LABELS[p]), values: means, hole: 0.73, marker: {colors: COLORS, line: {color: "#fff", width: 3}}, textinfo: "none", sort: false, direction: "clockwise", hovertemplate: "%{label}<br>%{value:.2f}%<extra></extra>"}], layout({margin: {l: 10, r: 10, t: 20, b: 35}, showlegend: true, legend: {orientation: "h", x: 0.5, xanchor: "center", y: -0.12, font: {size: 10}}, annotations: [{text: `<b>${NUM.format(available.length)}</b><br><span style='font-size:11px'>samples</span>`, x: 0.5, y: 0.5, showarrow: false, font: {size: 32, color: "#17273f"}}]}), config);
  } else emptyChart("composition-chart", "No samples with defined frequencies");
  const old = $("#selected-sample").value;
  $("#selected-sample").replaceChildren(...filtered.map((s) => new Option(`${s.sample} · ${s.subject}`, s.sample)));
  if (filtered.some((s) => s.sample === old)) $("#selected-sample").value = old;
  renderSelectedSample();
  renderFrequencyTable();
}

function emptyChart(id, message) {
  Plotly.purge(id);
  $(`#${id}`).innerHTML = `<div class="empty-chart">${escapeHTML(message)}</div>`;
}

function renderSelectedSample() {
  const sample = filtered.find((s) => s.sample === $("#selected-sample").value);
  if (!sample) { emptyChart("sample-chart", "No samples match these filters"); $("#sample-detail").textContent = "Adjust or reset your filters."; return; }
  if (!sample.total_count) emptyChart("sample-chart", "Zero total cells: percentages are undefined");
  else Plotly.react("sample-chart", [{type: "bar", orientation: "h", y: data.populations.map((p) => LABELS[p]), x: data.populations.map((p) => frequency(sample, p)), marker: {color: COLORS}, text: data.populations.map((p) => `${number(frequency(sample, p))}%`), textposition: "auto", hovertemplate: "%{y}<br>%{x:.2f}%<extra></extra>"}], layout({margin: {l: 98, r: 25, t: 18, b: 40}, bargap: 0.42, xaxis: {ticksuffix: "%", gridcolor: "#eef2f7", zeroline: false, rangemode: "tozero"}, yaxis: {autorange: "reversed", showgrid: false}}), config);
  $("#sample-detail").textContent = `${sample.project} · ${sample.condition} · ${sample.treatment} · ${sample.sample_type} · day ${sample.time_from_treatment_start} · ${NUM.format(sample.total_count)} total cells`;
}

function renderFrequencyTable() {
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SAMPLES));
  page = Math.max(0, Math.min(page, totalPages - 1));
  const selected = filtered.slice(page * PAGE_SAMPLES, (page + 1) * PAGE_SAMPLES);
  $("#table-count").textContent = `${NUM.format(filtered.length * 5)} rows · percentage = count ÷ total_count × 100`;
  $("#frequency-table tbody").innerHTML = selected.length ? selected.flatMap((s) => data.populations.map((p, i) => `<tr><td>${escapeHTML(s.sample)}</td><td>${NUM.format(s.total_count)}</td><td><span class="population-dot" style="background:${COLORS[i]}"></span>${p}</td><td>${NUM.format(s[p])}</td><td>${number(frequency(s, p))}${s.total_count ? "%" : ""}</td></tr>`)).join("") : '<tr><td colspan="5">No samples match these filters.</td></tr>';
  $("#page-label").textContent = `${NUM.format(filtered.length ? page * 25 + 1 : 0)}–${NUM.format(Math.min((page + 1) * 25, filtered.length * 5))} of ${NUM.format(filtered.length * 5)} rows`;
  $("#prev-page").disabled = page === 0;
  $("#next-page").disabled = page >= totalPages - 1;
  $("#download-filtered").disabled = !filtered.length;
}

function countList(selector, rows, labelKey, countKey, labels = {}) {
  $(selector).innerHTML = rows.map((row) => `<div class="count-row"><span>${escapeHTML(labels[row[labelKey]] || row[labelKey])}</span><strong>${NUM.format(row[countKey])}</strong></div>`).join("");
}

function donut(id, rows, key, labels, colors, total) {
  if (!total) {emptyChart(id, "No eligible subjects"); return;}
  Plotly.react(id, [{type: "pie", labels: rows.map((r) => labels[r[key]] || r[key]), values: rows.map((r) => r.subjects), hole: 0.78, textinfo: "none", sort: false, marker: {colors: rows.map((r) => colors[r[key]] || "#aab5c6"), line: {color: "#fff", width: 4}}, hovertemplate: "%{label}<br>%{value} subjects (%{percent})<extra></extra>"}], layout({showlegend: false, margin: {l: 20, r: 20, t: 15, b: 15}, annotations: [{text: `<b>${NUM.format(total)}</b><br><span style='font-size:11px'>subjects</span>`, x: 0.5, y: 0.5, showarrow: false, font: {size: 32, color: "#17273f"}}]}), config);
}

function renderBaseline() {
  const b = data.baseline;
  $("#baseline-metrics").innerHTML = metric("Baseline samples", NUM.format(b.sample_count), "Melanoma · miraclib · PBMC")
    + metric("Subjects", NUM.format(b.subject_count), "Counted once per subject")
    + metric("Projects", b.by_project.length, "With eligible baseline samples")
    + metric("Treatment day", "0", "Before treatment follow-up", true);
  Plotly.react("project-chart", [{type: "bar", x: b.by_project.map((p) => p.project), y: b.by_project.map((p) => p.samples), marker: {color: ["#628ced", "#a0b8ef"]}, text: b.by_project.map((p) => p.samples), textposition: "outside", cliponaxis: false, hovertemplate: "%{x}: %{y} samples<extra></extra>"}], layout({margin: {l: 45, r: 25, t: 28, b: 32}, bargap: 0.55, xaxis: {showgrid: false}, yaxis: {gridcolor: "#eef2f7", zeroline: false, rangemode: "tozero"}}), config);
  countList("#project-counts", b.by_project, "project", "samples");
  const responseLabels = {yes: "Responders", no: "Non-responders", unknown: "Unknown"};
  donut("baseline-response-chart", b.by_response, "response", responseLabels, RESPONSE_COLORS, b.subject_count);
  countList("#response-counts", b.by_response, "response", "subjects", responseLabels);
  donut("sex-chart", b.by_sex, "sex", {M: "Male", F: "Female"}, {M: "#759ac7", F: "#a6b9d5"}, b.subject_count);
  countList("#sex-counts", b.by_sex, "sex", "subjects", {M: "Male", F: "Female"});
  $("#bcell-answer").textContent = b.male_responder_b_cells.formatted;
  $("#bcell-n").textContent = `${NUM.format(b.male_responder_b_cells.samples)} samples · ${NUM.format(b.male_responder_b_cells.subjects)} subjects`;
  $("#baseline-table-note").textContent = `All ${NUM.format(b.sample_count)} matching samples. This table scrolls; the CSV contains the complete cohort.`;
  $("#baseline-table tbody").innerHTML = b.samples.map((s) => `<tr>${[s.sample, s.subject, s.project, s.response === "yes" ? "Responder" : s.response === "no" ? "Non-responder" : "Unknown", s.sex, s.age, s.time_from_treatment_start].map((v) => `<td>${escapeHTML(v)}</td>`).join("")}</tr>`).join("");
}

async function init() {
  try {
    const response = await fetch("data.json");
    if (!response.ok) throw new Error(response.status === 503 ? "Database is missing. Run make pipeline and restart the dashboard." : `Could not load study data (HTTP ${response.status}).`);
    data = await response.json();
    $("#loading").hidden = true;
    $("#content").hidden = false;
    $("#footer-detail").textContent = `${NUM.format(data.overview.samples)} samples · Source SHA-256 ${data.metadata.source_sha256.slice(0, 12)}`;
    setupFilters();
    ["response-window", "response-unit", "show-points"].forEach((id) => $(`#${id}`).addEventListener("change", renderResponse));
    window.addEventListener("hashchange", showView);
    showView();
  } catch (error) {
    $("#loading").hidden = true;
    $("#error").hidden = false;
    $("#error").textContent = `${error.message} If running locally, use make setup, make pipeline, then make dashboard.`;
  }
}
init();
