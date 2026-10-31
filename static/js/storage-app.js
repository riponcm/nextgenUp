/**
 * Saved files & storage (desktop app only).
 *
 * - header button -> panel with the save folder, automatic saving, and a
 *   one-click cleaner for leftover working files (never AI models)
 * - a toast that says where a finished result was saved
 *
 * (Keeping the computer awake during a job is done by the desktop shell.)
 */
(function () {
    'use strict';

    const $ = (s) => document.querySelector(s);
    const NGU_TOKEN = document.querySelector('meta[name="ngu-token"]')?.content || '';
    const JSON_HEADERS = { 'Content-Type': 'application/json', 'X-NGU-Token': NGU_TOKEN };

    const btn = $('#storage-btn');
    const overlay = $('#storage-overlay');
    if (!btn || !overlay) return;

    let settings = null;

    function fmtBytes(n) {
        if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(2) + ' GB';
        if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
        if (n >= 1024) return Math.round(n / 1024) + ' KB';
        return n + ' B';
    }

    function plural(n, word) { return `${n} ${word}${n === 1 ? '' : 's'}`; }

    function desktopDialog() {
        return (window.__TAURI__ && window.__TAURI__.dialog) ? window.__TAURI__.dialog : null;
    }

    // External links: a normal browser opens a tab; the desktop webview
    // blocks window.open, so ask the local server to open the system browser.
    function openExternal(url) {
        const w = window.open(url, '_blank', 'noopener');
        if (!w) {
            fetch('/api/open', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) })
                .catch(() => {});
        }
    }

    async function reveal(path) {
        try {
            await fetch('/api/reveal', {
                method: 'POST', headers: JSON_HEADERS,
                body: JSON.stringify(path ? { path } : {}),
            });
        } catch { /* ignore */ }
    }

    // ----------------------------- settings -----------------------------

    function showSettingsError(msg) {
        const el = $('#stor-settings-error');
        el.textContent = msg || '';
        el.classList.toggle('hidden', !msg);
    }

    function renderSettings() {
        if (!settings) return;
        $('#stor-auto').checked = !!settings.auto_save;
        $('#stor-path').textContent = settings.save_dir;
    }

    async function loadSettings() {
        const res = await fetch('/api/settings');
        if (!res.ok) throw new Error('settings unavailable');
        settings = await res.json();
        renderSettings();
        return settings;
    }

    async function saveSettings(patch) {
        showSettingsError('');
        try {
            const res = await fetch('/api/settings', {
                method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Could not save the setting');
            settings = data;
        } catch (e) {
            showSettingsError(e.message);
        }
        renderSettings();
    }

    async function chooseFolder() {
        const dlg = desktopDialog();
        let picked = null;
        if (dlg) {
            picked = await dlg.open({ directory: true, multiple: false, defaultPath: settings?.save_dir });
        } else {
            picked = window.prompt('Folder to save finished files in (full path):', settings?.save_dir || '');
        }
        if (picked) await saveSettings({ save_dir: picked });
    }

    // ------------------------------ storage ------------------------------

    async function refreshStorage() {
        const size = $('#stor-size');
        try {
            const res = await fetch('/api/storage');
            const r = await res.json();
            if (!res.ok) throw new Error(r.error || 'failed');
            const left = r.leftovers, uns = r.unsaved, prot = r.protected;
            size.textContent = left.files ? fmtBytes(left.bytes) : 'Nothing to clean';
            $('#stor-detail').textContent = left.files
                ? `${plural(left.files, 'file')} you can safely remove: copies of the files you opened, extra temporary files, and results already saved to your folder.`
                : 'No leftover files right now.';
            const row = $('#stor-unsaved-row');
            row.classList.toggle('hidden', !uns.files);
            if (uns.files) {
                $('#stor-unsaved-text').textContent =
                    `Also delete ${plural(uns.files, 'finished result')} not saved to your folder yet (${fmtBytes(uns.bytes)})`;
            } else {
                $('#stor-unsaved').checked = false;
            }
            const clean = $('#stor-clean');
            clean.disabled = !(left.files || (uns.files && $('#stor-unsaved').checked));
            if (prot.files) {
                $('#stor-detail').textContent +=
                    ` ${fmtBytes(prot.bytes)} from a running job or from the last few minutes is kept for now.`;
            }
        } catch {
            size.textContent = 'Could not read the storage details';
        }
    }

    async function cleanNow() {
        const clean = $('#stor-clean');
        clean.disabled = true;
        const out = $('#stor-clean-result');
        out.classList.add('hidden');
        try {
            const res = await fetch('/api/storage/clean', {
                method: 'POST', headers: JSON_HEADERS,
                body: JSON.stringify({ include_unsaved: $('#stor-unsaved').checked }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Cleaning failed');
            out.textContent = data.files
                ? `Freed ${fmtBytes(data.bytes)} (${plural(data.files, 'file')} removed).`
                : 'Nothing needed cleaning.';
            out.classList.remove('hidden');
        } catch (e) {
            out.textContent = e.message;
            out.classList.remove('hidden');
        }
        $('#stor-unsaved').checked = false;
        await refreshStorage();
    }

    function openPanel() {
        overlay.classList.remove('hidden');
        $('#stor-clean-result').classList.add('hidden');
        showSettingsError('');
        loadSettings().catch(() => {});
        refreshStorage();
    }

    function closePanel() { overlay.classList.add('hidden'); }

    // ------------------------- "saved to" toast -------------------------

    let toastTimer = null;

    function saveToast(text, revealPath, isError) {
        document.querySelector('.save-toast')?.remove();
        const t = document.createElement('div');
        t.className = 'save-toast' + (isError ? ' error' : '');
        const msg = document.createElement('span');
        msg.className = 'save-toast-name';
        msg.textContent = text;
        t.appendChild(msg);
        if (!isError) {
            const b = document.createElement('button');
            b.textContent = 'Show';
            b.addEventListener('click', () => reveal(revealPath));
            t.appendChild(b);
        }
        document.body.appendChild(t);
        requestAnimationFrame(() => t.classList.add('show'));
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            t.classList.remove('show');
            setTimeout(() => t.remove(), 300);
        }, isError ? 9000 : 8000);
    }

    let since = 0;

    async function pollSaves() {
        try {
            const res = await fetch('/api/saves/recent?since=' + since);
            if (!res.ok) return;
            const data = await res.json();
            const first = since === 0;
            since = data.now;
            if (first || !data.items.length) return;   // do not replay old saves
            const errors = data.items.filter((i) => i.error);
            const saved = data.items.filter((i) => !i.error);
            if (saved.length === 1) {
                saveToast('Saved: ' + saved[0].name, saved[0].path, false);
            } else if (saved.length > 1) {
                const folder = saved[saved.length - 1].path.replace(/[\\/][^\\/]*$/, '');
                saveToast(`Saved ${saved.length} files to ${folder}`, saved[saved.length - 1].path, false);
            }
            if (errors.length) {
                saveToast(`Could not save ${errors[0].name}: ${errors[0].error}`, null, true);
            }
        } catch { /* engine busy or restarting */ }
    }

    // ------------------------------- start -------------------------------

    async function init() {
        try {
            await loadSettings();
        } catch { return; }
        if (!settings.desktop) return;          // web mode keeps browser downloads

        btn.classList.remove('hidden');
        btn.addEventListener('click', openPanel);
        $('#storage-close').addEventListener('click', closePanel);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closePanel(); });
        $('#stor-auto').addEventListener('change', (e) => saveSettings({ auto_save: e.target.checked }));
        $('#stor-change').addEventListener('click', chooseFolder);
        $('#stor-open').addEventListener('click', () => reveal(null));
        $('#stor-clean').addEventListener('click', cleanNow);
        $('#stor-unsaved').addEventListener('change', refreshStorage);
        $('#stor-jharu-link').addEventListener('click', (e) => {
            e.preventDefault();
            openExternal('https://jharu.matily.org');
        });
        // Jharu only has Mac and Windows versions
        if (/Linux/i.test(navigator.userAgent) && !/Android/i.test(navigator.userAgent)) {
            $('#stor-jharu').classList.add('hidden');
        }

        pollSaves();
        setInterval(pollSaves, 4000);
    }

    init();
})();
