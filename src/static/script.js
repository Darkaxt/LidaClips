const syncControlButton = document.getElementById("sync-control-button");
const syncControlIcon = document.getElementById("sync-control-icon");

const lidarrAddress = document.getElementById("lidarr-address");
const navidromeAddress = document.getElementById("navidrome-address");
const clipOutputMode = document.getElementById("clip-output-mode");
const clipOutputPath = document.getElementById("clip-output-path");
const syncSchedule = document.getElementById("sync-schedule");
const syncArtistAllowlist = document.getElementById("sync-artist-allowlist");
const maxTargetsPerRun = document.getElementById("max-targets-per-run");
const downloadEnabled = document.getElementById("download-enabled");
const clientApiKey = document.getElementById("client-api-key");
const apiKeyRevealButton = document.getElementById("api-key-reveal-button");
const apiKeyRevealIcon = document.getElementById("api-key-reveal-icon");
const apiKeyCopyButton = document.getElementById("api-key-copy-button");

const dashboardActiveClips = document.getElementById("dashboard-active-clips");
const dashboardOfficialClips = document.getElementById("dashboard-official-clips");
const dashboardFallbackClips = document.getElementById("dashboard-fallback-clips");
const dashboardFailures = document.getElementById("dashboard-failures");
const dashboardRollout = document.getElementById("dashboard-rollout");
const dashboardLastUpdated = document.getElementById("dashboard-last-updated");
const dashboardTrackedTracks = document.getElementById("dashboard-tracked-tracks");
const dashboardScope = document.getElementById("dashboard-scope");
const dashboardScheduleSummary = document.getElementById("dashboard-schedule-summary");
const dashboardBatchSize = document.getElementById("dashboard-batch-size");
const dashboardDownloadState = document.getElementById("dashboard-download-state");
const recentClipsTable = document.getElementById("recent-clips-table").getElementsByTagName("tbody")[0];
const recentFailuresList = document.getElementById("recent-failures-list");

const socket = io();
let currentSettings = null;
let currentControl = { sync_paused: false, sync_running: false };
let currentApiKey = "";
let pendingApiKeyAction = null;

function formatScore(score) {
    if (score === null || score === undefined || score === "") {
        return "";
    }
    return Number(score).toFixed(0);
}

function formatTimestamp(value) {
    if (!value) {
        return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return date.toLocaleString([], {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function tierLabel(tier) {
    if (tier === "official") {
        return "Official";
    }
    if (tier === "fallback") {
        return "Fallback";
    }
    return tier || "Unknown";
}

function renderRollout() {
    if (!currentSettings) {
        dashboardRollout.textContent = currentControl.sync_paused ? "Paused" : "Loading";
        return;
    }
    const scope = (currentSettings.sync_artist_allowlist || []).length ? "Allowlist" : "Global";
    const state = currentControl.sync_paused ? "paused" : (currentSettings.download_enabled ? "enabled" : "dry run");
    dashboardRollout.textContent = `${scope}, ${state}`;
}

function renderGuardrails(settings) {
    const allowlist = settings.sync_artist_allowlist || [];
    dashboardScope.textContent = allowlist.length ? allowlist.join(", ") : "Global library";
    const schedule = settings.sync_schedule || [];
    dashboardScheduleSummary.textContent = schedule.length ? schedule.map((hour) => `${hour}:00`).join(", ") : "Manual only";
    dashboardBatchSize.textContent = `${settings.max_targets_per_run ?? 0} targets per run`;
    dashboardDownloadState.textContent = settings.download_enabled ? "Enabled" : "Dry run";
}

function renderControl(control) {
    if (!control) {
        return;
    }
    currentControl = control;
    const paused = Boolean(control.sync_paused);
    const running = Boolean(control.sync_running);
    syncControlButton.classList.toggle("sync-paused", paused);
    syncControlButton.classList.toggle("sync-running", running);
    syncControlButton.disabled = false;
    syncControlIcon.classList.toggle("fa-play", paused);
    syncControlIcon.classList.toggle("fa-pause", !paused);
    syncControlButton.title = paused ? "Resume sync" : (running ? "Pause after current sync" : "Pause sync");
    syncControlButton.setAttribute("aria-label", syncControlButton.title);
    renderRollout();
}

function renderDashboard(dashboard) {
    if (!dashboard) {
        return;
    }
    dashboardActiveClips.textContent = dashboard.active_clips || 0;
    dashboardOfficialClips.textContent = dashboard.official_clips || 0;
    dashboardFallbackClips.textContent = dashboard.fallback_clips || 0;
    dashboardFailures.textContent = dashboard.failures || 0;
    dashboardTrackedTracks.textContent = `${dashboard.tracked_tracks || 0} tracks known`;
    dashboardLastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;

    recentClipsTable.innerHTML = "";
    const clips = dashboard.recent_clips || [];
    if (!clips.length) {
        const row = recentClipsTable.insertRow();
        row.classList.add("empty-row");
        const cell = row.insertCell(0);
        cell.colSpan = 6;
        cell.textContent = "No clips have been indexed yet.";
    } else {
        clips.forEach((clip) => {
            const row = recentClipsTable.insertRow();
            const trackCell = row.insertCell(0);
            trackCell.textContent = `${clip.artist} - ${clip.track}`;
            trackCell.className = "clip-track-cell";
            const albumCell = row.insertCell(1);
            albumCell.textContent = clip.album || "";
            albumCell.className = "clip-album-cell";
            const tierCell = row.insertCell(2);
            tierCell.textContent = tierLabel(clip.quality_tier);
            tierCell.classList.add("text-center", `tier-${clip.quality_tier || "unknown"}`);
            const scoreCell = row.insertCell(3);
            scoreCell.textContent = formatScore(clip.score);
            scoreCell.classList.add("text-center");
            const addedCell = row.insertCell(4);
            addedCell.textContent = formatTimestamp(clip.created_at);
            addedCell.className = "clip-added-cell";
            const fileCell = row.insertCell(5);
            fileCell.textContent = clip.file_name || "";
            fileCell.className = "clip-file-cell";
        });
    }

    recentFailuresList.innerHTML = "";
    const failures = dashboard.recent_failures || [];
    if (!failures.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No recent failures.";
        recentFailuresList.appendChild(empty);
    } else {
        failures.forEach((failure) => {
            const item = document.createElement("div");
            item.className = "issue-item";
            const title = document.createElement("strong");
            title.textContent = failure.track ? `${failure.artist || "Unknown"} - ${failure.track}` : `Track ${failure.lidarr_track_id}`;
            const reason = document.createElement("span");
            reason.textContent = failure.reason || "unknown";
            const time = document.createElement("small");
            time.textContent = failure.updated_at || "";
            item.append(title, reason, time);
            recentFailuresList.appendChild(item);
        });
    }
}

function resetApiKeyControls() {
    currentApiKey = "";
    pendingApiKeyAction = null;
    hideApiKey();
    apiKeyRevealButton.disabled = false;
    apiKeyCopyButton.disabled = false;
}

function hideApiKey() {
    clientApiKey.type = "password";
    clientApiKey.value = "Hidden until revealed";
    apiKeyRevealIcon.classList.add("fa-eye");
    apiKeyRevealIcon.classList.remove("fa-eye-slash");
    apiKeyRevealButton.title = "Reveal API key";
    apiKeyRevealButton.setAttribute("aria-label", "Reveal API key");
}

function showApiKey() {
    clientApiKey.type = "text";
    clientApiKey.value = currentApiKey;
    apiKeyRevealIcon.classList.remove("fa-eye");
    apiKeyRevealIcon.classList.add("fa-eye-slash");
    apiKeyRevealButton.title = "Hide API key";
    apiKeyRevealButton.setAttribute("aria-label", "Hide API key");
}

function requestApiKey(action) {
    pendingApiKeyAction = action;
    if (action === "copy") {
        apiKeyCopyButton.disabled = true;
    }
    if (action === "reveal") {
        apiKeyRevealButton.disabled = true;
    }
    socket.emit("load_api_key");
}

async function copyApiKeyToClipboard(apiKey) {
    if (!apiKey) {
        showToast("API key unavailable", "No LidaClips client API key is configured.");
        return;
    }
    try {
        await navigator.clipboard.writeText(apiKey);
    } catch (_error) {
        const temporaryInput = document.createElement("textarea");
        temporaryInput.value = apiKey;
        temporaryInput.setAttribute("readonly", "");
        temporaryInput.style.position = "fixed";
        temporaryInput.style.left = "-9999px";
        document.body.appendChild(temporaryInput);
        temporaryInput.select();
        document.execCommand("copy");
        temporaryInput.remove();
    }
    showToast("API key copied", "Use it as X-Api-Key, apiKey, or api_key for LidaClips clients.");
}

syncControlButton.addEventListener("click", () => {
    syncControlButton.disabled = true;
    socket.emit("set_sync_paused", { sync_paused: !currentControl.sync_paused });
});

document.getElementById("config-modal").addEventListener("show.bs.modal", () => {
    resetApiKeyControls();
    socket.emit("load_settings");
});

apiKeyRevealButton.addEventListener("click", () => {
    if (clientApiKey.type === "text") {
        hideApiKey();
        return;
    }
    if (currentApiKey) {
        showApiKey();
        return;
    }
    requestApiKey("reveal");
});

apiKeyCopyButton.addEventListener("click", async () => {
    if (!currentApiKey) {
        requestApiKey("copy");
        return;
    }
    await copyApiKeyToClipboard(currentApiKey);
});

socket.on("settings_loaded", (settings) => {
    currentSettings = settings;
    lidarrAddress.value = settings.lidarr_address || "";
    navidromeAddress.value = settings.navidrome_address || "";
    clipOutputMode.value = settings.clip_output_mode || "";
    clipOutputPath.value = settings.clip_output_path || "";
    syncSchedule.value = (settings.sync_schedule || []).join(", ");
    syncArtistAllowlist.value = (settings.sync_artist_allowlist || []).join(", ");
    maxTargetsPerRun.value = settings.max_targets_per_run ?? "";
    downloadEnabled.value = settings.download_enabled ? "true" : "false";
    renderGuardrails(settings);
    renderRollout();
});

socket.on("state_update", (state) => {
    renderControl(state.control);
    renderDashboard(state.dashboard);
});

socket.on("dashboard_loaded", (dashboard) => {
    renderDashboard(dashboard);
});

socket.on("control_loaded", (control) => {
    renderControl(control);
});

socket.on("api_key_loaded", (payload) => {
    currentApiKey = (payload && payload.api_key) || "";
    const action = pendingApiKeyAction;
    pendingApiKeyAction = null;
    apiKeyRevealButton.disabled = false;
    apiKeyCopyButton.disabled = false;
    if (action === "copy") {
        copyApiKeyToClipboard(currentApiKey);
        return;
    }
    if (action === "reveal") {
        showApiKey();
    }
});

socket.on("new_toast_msg", (data) => {
    showToast(data.title, data.message);
});

function showToast(header, message) {
    const toastContainer = document.querySelector(".toast-container");
    const toastTemplate = document.getElementById("toast-template").cloneNode(true);
    toastTemplate.classList.remove("d-none");
    toastTemplate.querySelector(".toast-header strong").textContent = header;
    toastTemplate.querySelector(".toast-body").textContent = message;
    toastTemplate.querySelector(".text-muted").textContent = new Date().toLocaleString();
    toastContainer.appendChild(toastTemplate);
    const toast = new bootstrap.Toast(toastTemplate);
    toast.show();
    toastTemplate.addEventListener("hidden.bs.toast", () => {
        toastTemplate.remove();
    });
}

const themeSwitch = document.getElementById("theme-switch");
const savedTheme = localStorage.getItem("theme");
const savedSwitchPosition = localStorage.getItem("switchPosition");

if (savedSwitchPosition) {
    themeSwitch.checked = savedSwitchPosition === "true";
}

if (savedTheme) {
    document.documentElement.setAttribute("data-bs-theme", savedTheme);
}

themeSwitch.addEventListener("click", () => {
    if (document.documentElement.getAttribute("data-bs-theme") === "dark") {
        document.documentElement.setAttribute("data-bs-theme", "light");
    } else {
        document.documentElement.setAttribute("data-bs-theme", "dark");
    }
    localStorage.setItem("theme", document.documentElement.getAttribute("data-bs-theme"));
    localStorage.setItem("switchPosition", themeSwitch.checked);
});

socket.on("connect", () => {
    socket.emit("load_settings");
    socket.emit("load_dashboard");
    socket.emit("load_control");
});

setInterval(() => {
    if (socket.connected) {
        socket.emit("load_dashboard");
        socket.emit("load_control");
    }
}, 30000);
