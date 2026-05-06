const refreshTargetsButton = document.getElementById("refresh-targets-btn");
const targetsSpinner = document.getElementById("targets-spinner");
const targetsTable = document.getElementById("targets-table").getElementsByTagName("tbody")[0];
const targetsProgressBar = document.querySelector("#targets-progress-status-bar .progress-bar-striped");

const startSyncButton = document.getElementById("start-sync-btn");
const syncSpinner = document.getElementById("sync-spinner");
const syncProgressBar = document.querySelector("#sync-progress-status-bar .progress-bar-striped");
const clipsTable = document.getElementById("clips-table").getElementsByTagName("tbody")[0];

const lidarrAddress = document.getElementById("lidarr-address");
const navidromeAddress = document.getElementById("navidrome-address");
const clipOutputMode = document.getElementById("clip-output-mode");
const clipOutputPath = document.getElementById("clip-output-path");
const syncSchedule = document.getElementById("sync-schedule");
const syncArtistAllowlist = document.getElementById("sync-artist-allowlist");
const maxTargetsPerRun = document.getElementById("max-targets-per-run");
const downloadEnabled = document.getElementById("download-enabled");

const summaryTargets = document.getElementById("summary-targets");
const summaryDownloaded = document.getElementById("summary-downloaded");
const summaryNoMatch = document.getElementById("summary-no-match");
const summaryErrors = document.getElementById("summary-errors");

const dashboardActiveClips = document.getElementById("dashboard-active-clips");
const dashboardOfficialClips = document.getElementById("dashboard-official-clips");
const dashboardFallbackClips = document.getElementById("dashboard-fallback-clips");
const dashboardFailures = document.getElementById("dashboard-failures");
const dashboardRollout = document.getElementById("dashboard-rollout");
const dashboardLastUpdated = document.getElementById("dashboard-last-updated");
const dashboardTrackedTracks = document.getElementById("dashboard-tracked-tracks");
const recentClipsTable = document.getElementById("recent-clips-table").getElementsByTagName("tbody")[0];
const recentFailuresList = document.getElementById("recent-failures-list");

const socket = io();

function setProgressState(progressBar, status) {
    progressBar.classList.remove("bg-primary", "bg-danger", "bg-dark", "bg-warning", "bg-success");
    if (status === "busy" || status === "running") {
        progressBar.classList.add("bg-success", "progress-bar-animated");
        progressBar.style.width = "66%";
    } else if (status === "error") {
        progressBar.classList.add("bg-danger");
        progressBar.style.width = "100%";
    } else if (status === "complete") {
        progressBar.classList.add("bg-dark");
        progressBar.style.width = "100%";
    } else {
        progressBar.classList.add("bg-primary");
        progressBar.style.width = "0%";
    }
}

function formatDuration(seconds) {
    if (!seconds) {
        return "";
    }
    const minutes = Math.floor(seconds / 60);
    const remainder = String(seconds % 60).padStart(2, "0");
    return `${minutes}:${remainder}`;
}

function formatScore(score) {
    if (score === null || score === undefined || score === "") {
        return "";
    }
    return Number(score).toFixed(0);
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
        cell.colSpan = 5;
        cell.textContent = "No clips have been indexed yet.";
    } else {
        clips.forEach((clip) => {
            const row = recentClipsTable.insertRow();
            row.insertCell(0).textContent = `${clip.artist} - ${clip.track}`;
            row.insertCell(1).textContent = clip.album || "";
            const tierCell = row.insertCell(2);
            tierCell.textContent = tierLabel(clip.quality_tier);
            tierCell.classList.add("text-center", `tier-${clip.quality_tier || "unknown"}`);
            const scoreCell = row.insertCell(3);
            scoreCell.textContent = formatScore(clip.score);
            scoreCell.classList.add("text-center");
            row.insertCell(4).textContent = clip.file_name || "";
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

refreshTargetsButton.addEventListener("click", () => {
    socket.emit("refresh_targets");
});

startSyncButton.addEventListener("click", () => {
    socket.emit("start_sync");
});

document.getElementById("config-modal").addEventListener("show.bs.modal", () => {
    socket.emit("load_settings");
});

socket.on("settings_loaded", (settings) => {
    lidarrAddress.value = settings.lidarr_address || "";
    navidromeAddress.value = settings.navidrome_address || "";
    clipOutputMode.value = settings.clip_output_mode || "";
    clipOutputPath.value = settings.clip_output_path || "";
    syncSchedule.value = (settings.sync_schedule || []).join(", ");
    syncArtistAllowlist.value = (settings.sync_artist_allowlist || []).join(", ");
    maxTargetsPerRun.value = settings.max_targets_per_run ?? "";
    downloadEnabled.value = settings.download_enabled ? "true" : "false";
    const scope = (settings.sync_artist_allowlist || []).length ? "Allowlist" : "Global";
    const state = settings.download_enabled ? "enabled" : "paused";
    dashboardRollout.textContent = `${scope}, ${state}`;
});

socket.on("state_update", (state) => {
    const targetBusy = state.targets_status === "busy";
    refreshTargetsButton.disabled = targetBusy;
    targetsSpinner.classList.toggle("d-none", !targetBusy);
    setProgressState(targetsProgressBar, state.targets_status);

    targetsTable.innerHTML = "";
    (state.targets || []).forEach((target) => {
        const row = targetsTable.insertRow();
        row.insertCell(0).textContent = `${target.artist} - ${target.title}`;
        row.insertCell(1).textContent = target.album;
        const durationCell = row.insertCell(2);
        durationCell.textContent = formatDuration(target.duration);
        durationCell.classList.add("text-center");
    });

    const syncBusy = state.sync_status === "running";
    startSyncButton.disabled = syncBusy;
    syncSpinner.classList.toggle("d-none", !syncBusy);
    setProgressState(syncProgressBar, state.sync_status);

    const summary = state.summary || {};
    summaryTargets.textContent = summary.targets || 0;
    summaryDownloaded.textContent = summary.downloaded || 0;
    summaryNoMatch.textContent = summary.no_match || 0;
    summaryErrors.textContent = (summary.download_errors || 0) + (summary.navidrome_missing || 0);

    clipsTable.innerHTML = "";
    Object.entries(summary).forEach(([key, value]) => {
        const row = clipsTable.insertRow();
        row.insertCell(0).textContent = key.replaceAll("_", " ");
        const valueCell = row.insertCell(1);
        valueCell.textContent = value;
        valueCell.classList.add("text-center");
    });

    renderDashboard(state.dashboard);
});

socket.on("dashboard_loaded", (dashboard) => {
    renderDashboard(dashboard);
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
});

setInterval(() => {
    if (socket.connected) {
        socket.emit("load_dashboard");
    }
}, 30000);
