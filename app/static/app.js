// ComfyUI Studio Frontend Application Logic

// Global State
const state = {
    clientId: getOrCreateClientId(),
    ws: null,
    activeTab: 'generator',
    isGenerating: false,
    generationStartTime: null,
    generationTimerInterval: null,
    currentPreviewData: null,
    galleryImages: [],
    galleryOffset: 0,
    galleryLimit: 48,
    galleryTotal: 0,
    galleryLoading: false,
    searchTimeout: null,
    currentModalImage: null,
    comfyConnected: false,
};

// Utilities
function getOrCreateClientId() {
    let id = localStorage.getItem('comfyui_client_id');
    if (!id) {
        id = 'client_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
        localStorage.setItem('comfyui_client_id', id);
    }
    return id;
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    const bgClass = type === 'error' ? 'bg-rose-950/90 border-rose-800 text-rose-200' :
                    type === 'success' ? 'bg-emerald-950/90 border-emerald-800 text-emerald-200' :
                    'bg-zinc-900/90 border-zinc-700 text-zinc-200';

    toast.className = `pointer-events-auto flex items-center space-x-2 px-4 py-3 rounded-xl border text-xs shadow-xl backdrop-blur transition-all duration-300 transform translate-y-2 opacity-0 ${bgClass}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.remove('translate-y-2', 'opacity-0');
    });

    setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-2');
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// Navigation & Tab Switching
function switchTab(tabId) {
    state.activeTab = tabId;
    ['generator', 'gallery', 'workflow'].forEach(tab => {
        const view = document.getElementById(`view-${tab}`);
        const btn = document.getElementById(`tab-${tab}`);
        if (tab === tabId) {
            view.classList.remove('hidden');
            btn.className = 'flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all text-white bg-zinc-800 shadow-sm';
        } else {
            view.classList.add('hidden');
            btn.className = 'flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50';
        }
    });

    if (tabId === 'gallery') {
        loadGallery(true);
    } else if (tabId === 'workflow') {
        loadWorkflowTemplate();
    }
}

// Check ComfyUI Status & Load Models
async function checkComfyStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const badge = document.getElementById('comfy-status-badge');
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('status-text');

        if (data.comfyui && data.comfyui.connected) {
            state.comfyConnected = true;
            dot.className = 'w-2 h-2 rounded-full bg-emerald-400';
            text.innerText = 'ComfyUI Online';
            badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium bg-emerald-950/60 border border-emerald-800/80 text-emerald-300';
        } else {
            state.comfyConnected = false;
            dot.className = 'w-2 h-2 rounded-full bg-rose-500';
            text.innerText = 'ComfyUI Offline';
            badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium bg-rose-950/60 border border-rose-800/80 text-rose-300';
        }

        if (data.total_images !== undefined) {
            document.getElementById('gallery-count-badge').innerText = data.total_images;
        }
    } catch (e) {
        state.comfyConnected = false;
        document.getElementById('status-dot').className = 'w-2 h-2 rounded-full bg-rose-500';
        document.getElementById('status-text').innerText = 'Backend Offline';
    }
}

async function loadModels() {
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        const select = document.getElementById('ckpt-select');
        select.innerHTML = '<option value="">(Default from Workflow)</option>';
        if (data.models && data.models.length > 0) {
            data.models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                select.appendChild(opt);
            });
        }
    } catch (e) {
        console.warn('Could not load models list:', e);
    }
}

// WebSocket Management
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${state.clientId}`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        console.log('Connected to backend WebSocket');
    };

    state.ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error('Error parsing WS message:', e);
        }
    };

    state.ws.onclose = () => {
        console.warn('WebSocket closed, attempting reconnect in 2s...');
        setTimeout(initWebSocket, 2000);
    };

    state.ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };

    // Keepalive ping every 25s
    setInterval(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);
}

function handleWsMessage(msg) {
    // 1. Gallery updates
    if (msg.type === 'gallery_item_added') {
        onGalleryItemAdded(msg.item);
    } else if (msg.type === 'gallery_item_deleted') {
        onGalleryItemDeleted(msg.relative_path);
    }

    // 2. Generation progress events
    if (msg.channel === 'generation') {
        const data = msg.data || {};
        const params = msg.resolved_params || {};

        if (data.type === 'status') {
            const queue = data.queue_remaining;
            document.getElementById('progress-node-label').innerText = queue > 0 ? `In Queue (position: ${queue})...` : 'Starting generation...';
            document.getElementById('preview-status-pill').innerText = 'Queued';
        } else if (data.type === 'execution_start') {
            document.getElementById('progress-node-label').innerText = 'Initializing nodes...';
            document.getElementById('preview-status-pill').innerText = 'Running';
        } else if (data.type === 'executing') {
            document.getElementById('progress-node-label').innerText = `Executing node #${data.node}...`;
        } else if (data.type === 'progress') {
            const pct = data.percentage || 0;
            document.getElementById('progress-bar').style.width = `${pct}%`;
            document.getElementById('progress-percentage').innerText = `${pct}%`;
            document.getElementById('progress-node-label').innerText = `Sampling: step ${data.value}/${data.max} (${pct}%)`;
            document.getElementById('preview-status-pill').innerText = `Step ${data.value}/${data.max}`;
        } else if (data.type === 'images_ready') {
            if (data.images && data.images.length > 0) {
                const imgInfo = data.images[0];
                const relPath = (imgInfo.subfolder ? imgInfo.subfolder + '/' : '') + imgInfo.filename;
                displayGeneratedImage(relPath, params);
            }
        } else if (data.type === 'complete') {
            finishGeneration();
        } else if (data.type === 'error') {
            finishGeneration();
            showToast(`Generation error: ${data.error}`, 'error');
            document.getElementById('preview-status-pill').innerText = 'Error';
            document.getElementById('progress-node-label').innerText = `Error: ${data.error}`;
        }
    }
}

// Generator Logic
function toggleSeedInput() {
    const isRandom = document.getElementById('seed-randomize').checked;
    const input = document.getElementById('seed-input');
    input.disabled = isRandom;
    if (isRandom) {
        input.classList.add('opacity-50');
    } else {
        input.classList.remove('opacity-50');
    }
}

function rollNewSeed() {
    const newSeed = Math.floor(Math.random() * 1000000000000);
    const input = document.getElementById('seed-input');
    input.value = newSeed;
    document.getElementById('seed-randomize').checked = false;
    toggleSeedInput();
    showToast(`Rolled seed: ${newSeed}`, 'info');
}

function setResolution(w, h) {
    document.getElementById('width-input').value = w;
    document.getElementById('height-input').value = h;
}

function toggleAdvancedAccordion() {
    const content = document.getElementById('adv-content');
    const arrow = document.getElementById('adv-arrow');
    const isHidden = content.classList.contains('hidden');
    if (isHidden) {
        content.classList.remove('hidden');
        arrow.classList.add('rotate-180');
    } else {
        content.classList.add('hidden');
        arrow.classList.remove('rotate-180');
    }
}

function clearInput(id) {
    document.getElementById(id).value = '';
}

async function handleGenerate() {
    if (state.isGenerating) return;

    const positivePrompt = document.getElementById('positive-prompt').value.trim();
    const negativePrompt = document.getElementById('negative-prompt').value.trim();
    const isRandomSeed = document.getElementById('seed-randomize').checked;
    const rawSeed = document.getElementById('seed-input').value;
    const seed = isRandomSeed ? null : parseInt(rawSeed, 10);

    if (!positivePrompt) {
        showToast('Please enter a positive prompt', 'error');
        document.getElementById('positive-prompt').focus();
        return;
    }

    const payload = {
        positive_prompt: positivePrompt,
        negative_prompt: negativePrompt,
        seed: seed,
        randomize_seed: isRandomSeed,
        client_id: state.clientId,
        steps: parseInt(document.getElementById('steps-slider').value, 10),
        cfg: parseFloat(document.getElementById('cfg-slider').value),
        width: parseInt(document.getElementById('width-input').value, 10) || null,
        height: parseInt(document.getElementById('height-input').value, 10) || null,
        ckpt_name: document.getElementById('ckpt-select').value || null,
        sampler_name: document.getElementById('sampler-select').value || null,
        scheduler: document.getElementById('scheduler-select').value || null,
    };

    // Set UI to generating state
    state.isGenerating = true;
    startGenerationTimer();

    const btnGen = document.getElementById('btn-generate');
    const btnInterrupt = document.getElementById('btn-interrupt');
    const genText = document.getElementById('generate-text');
    btnGen.disabled = true;
    genText.innerText = 'Queuing...';
    btnInterrupt.classList.remove('hidden');

    document.getElementById('progress-container').classList.remove('hidden');
    document.getElementById('progress-bar').style.width = '0%';
    document.getElementById('progress-percentage').innerText = '0%';
    document.getElementById('progress-node-label').innerText = 'Contacting ComfyUI...';
    document.getElementById('preview-status-pill').innerText = 'Queuing';

    try {
        const res = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to submit generation');
        }

        const data = await res.json();
        genText.innerText = 'Generating...';

        if (data.resolved_params && data.resolved_params.seed) {
            document.getElementById('seed-input').value = data.resolved_params.seed;
        }
    } catch (e) {
        finishGeneration();
        showToast(e.message, 'error');
        document.getElementById('preview-status-pill').innerText = 'Failed';
        document.getElementById('progress-node-label').innerText = `Error: ${e.message}`;
    }
}

async function handleInterrupt() {
    try {
        await fetch('/api/interrupt', { method: 'POST' });
        showToast('Cancel request sent to ComfyUI', 'info');
    } catch (e) {
        showToast('Failed to interrupt ComfyUI', 'error');
    }
}

function startGenerationTimer() {
    state.generationStartTime = Date.now();
    const timerElem = document.getElementById('gen-timer');
    timerElem.innerText = '0.0s';
    state.generationTimerInterval = setInterval(() => {
        const elapsed = ((Date.now() - state.generationStartTime) / 1000).toFixed(1);
        timerElem.innerText = `${elapsed}s`;
    }, 100);
}

function finishGeneration() {
    state.isGenerating = false;
    clearInterval(state.generationTimerInterval);

    const btnGen = document.getElementById('btn-generate');
    const btnInterrupt = document.getElementById('btn-interrupt');
    const genText = document.getElementById('generate-text');

    btnGen.disabled = false;
    genText.innerText = 'Generate Image';
    btnInterrupt.classList.add('hidden');
}

function displayGeneratedImage(relative_path, params = {}) {
    state.currentPreviewData = { relative_path, params };

    const imgUrl = `/api/images/${encodeURIComponent(relative_path)}`;
    const previewImg = document.getElementById('preview-img');
    const placeholder = document.getElementById('preview-placeholder');
    const wrapper = document.getElementById('preview-image-wrapper');
    const footer = document.getElementById('preview-meta-footer');

    previewImg.src = imgUrl;
    placeholder.classList.add('hidden');
    wrapper.classList.remove('hidden');
    footer.classList.remove('hidden');

    document.getElementById('preview-status-pill').innerText = 'Completed';
    document.getElementById('progress-bar').style.width = '100%';
    document.getElementById('progress-percentage').innerText = '100%';
    document.getElementById('progress-node-label').innerText = 'Generation complete!';

    if (params.seed) {
        document.getElementById('preview-seed-badge').innerText = `Seed: ${params.seed}`;
    }
    document.getElementById('preview-filename-badge').innerText = relative_path;

    showToast('Image generated successfully!', 'success');
}

function openCurrentInModal() {
    if (state.currentPreviewData && state.currentPreviewData.relative_path) {
        openImageModal(state.currentPreviewData.relative_path);
    }
}

function downloadCurrentPreview() {
    if (state.currentPreviewData && state.currentPreviewData.relative_path) {
        const a = document.createElement('a');
        a.href = `/api/images/${encodeURIComponent(state.currentPreviewData.relative_path)}`;
        a.download = state.currentPreviewData.relative_path.split('/').pop();
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
}

// Gallery View Logic
function debounceGallerySearch() {
    clearTimeout(state.searchTimeout);
    state.searchTimeout = setTimeout(() => {
        loadGallery(true);
    }, 300);
}

async function loadGallery(reset = false) {
    if (reset) {
        state.galleryOffset = 0;
        state.galleryImages = [];
        document.getElementById('gallery-grid').innerHTML = '';
    }

    state.galleryLoading = true;
    const query = document.getElementById('gallery-search').value.trim();
    const sort = document.getElementById('gallery-sort').value;
    const statsText = document.getElementById('gallery-stats-text');
    statsText.innerText = 'Loading images...';

    try {
        const url = `/api/images?limit=${state.galleryLimit}&offset=${state.galleryOffset}&query=${encodeURIComponent(query)}&sort=${sort}`;
        const res = await fetch(url);
        const data = await res.json();

        state.galleryTotal = data.total;
        document.getElementById('gallery-count-badge').innerText = data.total;
        statsText.innerText = `${data.total} image${data.total === 1 ? '' : 's'} in output volume`;

        if (reset) {
            state.galleryImages = data.items;
        } else {
            state.galleryImages = state.galleryImages.concat(data.items);
        }

        renderGalleryGrid();

        // Load More button visibility
        const loadMoreContainer = document.getElementById('gallery-load-more-container');
        if (state.galleryImages.length < data.total) {
            loadMoreContainer.classList.remove('hidden');
        } else {
            loadMoreContainer.classList.add('hidden');
        }

        // Empty state visibility
        const emptyState = document.getElementById('gallery-empty');
        if (data.total === 0) {
            emptyState.classList.remove('hidden');
        } else {
            emptyState.classList.add('hidden');
        }

    } catch (e) {
        console.error('Error loading gallery:', e);
        statsText.innerText = 'Failed to load gallery';
    } finally {
        state.galleryLoading = false;
    }
}

function loadMoreGallery() {
    if (state.galleryLoading) return;
    state.galleryOffset += state.galleryLimit;
    loadGallery(false);
}

function renderGalleryGrid() {
    const grid = document.getElementById('gallery-grid');
    grid.innerHTML = '';

    state.galleryImages.forEach(item => {
        const card = createGalleryCard(item);
        grid.appendChild(card);
    });
}

function createGalleryCard(item) {
    const card = document.createElement('div');
    card.id = `gallery-card-${sanitizeId(item.relative_path)}`;
    card.className = 'group relative aspect-square bg-zinc-900 rounded-xl overflow-hidden border border-zinc-800/80 hover:border-brand-500/80 transition-all duration-200 cursor-pointer shadow-sm hover:shadow-xl hover:shadow-brand-500/10';

    const thumbUrl = `/api/thumbnails/${encodeURIComponent(item.relative_path)}`;

    card.innerHTML = `
        <img src="${thumbUrl}" alt="${item.filename}" loading="lazy" class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105">
        
        <!-- Gradient Overlay -->
        <div class="absolute inset-0 bg-gradient-to-t from-black/85 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity p-2.5 flex flex-col justify-end">
            <span class="text-xs font-semibold text-white truncate drop-shadow">${item.filename}</span>
            <div class="flex items-center justify-between text-[10px] text-zinc-300 mt-1">
                <span>${item.file_size_human}</span>
                <span class="bg-black/50 px-1.5 py-0.5 rounded text-zinc-300">View</span>
            </div>
        </div>
    `;

    card.onclick = () => openImageModal(item.relative_path);
    return card;
}

function sanitizeId(path) {
    return path.replace(/[^a-zA-Z0-9-_]/g, '_');
}

function onGalleryItemAdded(item) {
    // Increment badge
    const badge = document.getElementById('gallery-count-badge');
    let count = parseInt(badge.innerText, 10) || 0;
    badge.innerText = count + 1;

    // Prepend to gallery if active or in memory
    const existingIndex = state.galleryImages.findIndex(i => i.relative_path === item.relative_path);
    if (existingIndex === -1) {
        state.galleryImages.unshift(item);
        const grid = document.getElementById('gallery-grid');
        const empty = document.getElementById('gallery-empty');
        empty.classList.add('hidden');

        const card = createGalleryCard(item);
        grid.insertBefore(card, grid.firstChild);
    }
}

function onGalleryItemDeleted(relative_path) {
    // Decrement badge
    const badge = document.getElementById('gallery-count-badge');
    let count = parseInt(badge.innerText, 10) || 0;
    if (count > 0) badge.innerText = count - 1;

    // Remove from in-memory list
    state.galleryImages = state.galleryImages.filter(i => i.relative_path !== relative_path);

    // Remove DOM card
    const card = document.getElementById(`gallery-card-${sanitizeId(relative_path)}`);
    if (card) {
        card.remove();
    }

    if (state.galleryImages.length === 0) {
        document.getElementById('gallery-empty').classList.remove('hidden');
    }
}

// Image Detail Modal Logic
async function openImageModal(relativePath) {
    state.currentModalImage = null;
    const modal = document.getElementById('image-modal');
    modal.classList.remove('hidden');

    const modalImg = document.getElementById('modal-img');
    const modalFilename = document.getElementById('modal-filename');
    const modalDate = document.getElementById('modal-date');
    const modalDim = document.getElementById('modal-img-dim');
    const modalDownload = document.getElementById('modal-download-link');

    // Reset fields
    modalImg.src = '';
    modalFilename.innerText = relativePath.split('/').pop();
    modalDate.innerText = 'Loading metadata...';
    modalDim.innerText = '';
    document.getElementById('modal-pos-prompt').innerText = 'Loading...';
    document.getElementById('modal-neg-prompt').innerText = 'Loading...';
    document.getElementById('modal-seed').innerText = '-';
    document.getElementById('modal-steps').innerText = '-';
    document.getElementById('modal-cfg').innerText = '-';
    document.getElementById('modal-sampler').innerText = '-';
    document.getElementById('modal-model').innerText = '-';
    document.getElementById('modal-raw-json').innerText = '{}';

    const fullUrl = `/api/images/${encodeURIComponent(relativePath)}`;
    modalImg.src = fullUrl;
    modalDownload.href = fullUrl;
    modalDownload.download = relativePath.split('/').pop();

    try {
        const res = await fetch(`/api/images/${encodeURIComponent(relativePath)}/metadata`);
        if (!res.ok) throw new Error('Metadata not found');
        const meta = await res.json();
        state.currentModalImage = meta;

        modalFilename.innerText = meta.filename;
        modalDate.innerText = `${meta.modified_at ? new Date(meta.modified_at).toLocaleString() : ''} · ${meta.file_size_human}`;
        modalDim.innerText = meta.dimensions || '';

        const p = meta.parsed || {};
        document.getElementById('modal-pos-prompt').innerText = p.positive_prompt || '(None found)';
        document.getElementById('modal-neg-prompt').innerText = p.negative_prompt || '(None)';
        document.getElementById('modal-seed').innerText = p.seed !== undefined && p.seed !== null ? p.seed : '-';
        document.getElementById('modal-steps').innerText = p.steps || '-';
        document.getElementById('modal-cfg').innerText = p.cfg || '-';
        document.getElementById('modal-sampler').innerText = p.sampler_name ? `${p.sampler_name} / ${p.scheduler || 'normal'}` : '-';
        document.getElementById('modal-model').innerText = p.model || '(Standard)';

        document.getElementById('modal-raw-json').innerText = JSON.stringify(meta.raw || {}, null, 2);

    } catch (e) {
        console.warn('Metadata fetch error:', e);
        modalDate.innerText = 'No metadata embedded';
        document.getElementById('modal-pos-prompt').innerText = '(No metadata chunk found)';
    }
}

function closeImageModal() {
    document.getElementById('image-modal').classList.add('hidden');
    state.currentModalImage = null;
}

function toggleModalRawJson() {
    const container = document.getElementById('modal-raw-container');
    const arrow = document.getElementById('raw-toggle-arrow');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        arrow.innerText = '▲';
    } else {
        container.classList.add('hidden');
        arrow.innerText = '▼';
    }
}

function copyModalPositive() {
    const text = document.getElementById('modal-pos-prompt').innerText;
    navigator.clipboard.writeText(text);
    showToast('Positive prompt copied to clipboard', 'info');
}

function copyModalNegative() {
    const text = document.getElementById('modal-neg-prompt').innerText;
    navigator.clipboard.writeText(text);
    showToast('Negative prompt copied to clipboard', 'info');
}

function copyModalRawJson() {
    const text = document.getElementById('modal-raw-json').innerText;
    navigator.clipboard.writeText(text);
    showToast('Raw JSON copied to clipboard', 'info');
}

function loadModalPromptIntoGenerator() {
    if (!state.currentModalImage) return;
    const p = state.currentModalImage.parsed || {};

    if (p.positive_prompt) {
        document.getElementById('positive-prompt').value = p.positive_prompt;
    }
    if (p.negative_prompt) {
        document.getElementById('negative-prompt').value = p.negative_prompt;
    }
    if (p.seed !== undefined && p.seed !== null) {
        document.getElementById('seed-input').value = p.seed;
        document.getElementById('seed-randomize').checked = false;
        toggleSeedInput();
    }
    if (p.steps) {
        document.getElementById('steps-slider').value = p.steps;
        document.getElementById('steps-val').innerText = p.steps;
    }
    if (p.cfg) {
        document.getElementById('cfg-slider').value = p.cfg;
        document.getElementById('cfg-val').innerText = p.cfg;
    }
    if (p.width && p.height) {
        setResolution(p.width, p.height);
    }
    if (p.sampler_name) {
        document.getElementById('sampler-select').value = p.sampler_name;
    }
    if (p.scheduler) {
        document.getElementById('scheduler-select').value = p.scheduler;
    }

    closeImageModal();
    switchTab('generator');
    showToast('Loaded parameters into Generator', 'success');
}

async function confirmDeleteModalImage() {
    if (!state.currentModalImage) return;
    const relPath = state.currentModalImage.relative_path || state.currentModalImage.filename;
    const filename = state.currentModalImage.filename;

    if (!confirm(`Permanently delete "${filename}" from the mounted output directory?\n\nThis cannot be undone.`)) {
        return;
    }

    try {
        const res = await fetch(`/api/images/${encodeURIComponent(relPath)}`, {
            method: 'DELETE'
        });

        if (!res.ok) {
            throw new Error('Failed to delete image');
        }

        showToast(`Deleted ${filename}`, 'success');
        closeImageModal();

        // Also reset generator preview if it was showing the deleted image
        if (state.currentPreviewData && state.currentPreviewData.relative_path === relPath) {
            document.getElementById('preview-image-wrapper').classList.add('hidden');
            document.getElementById('preview-meta-footer').classList.add('hidden');
            document.getElementById('preview-placeholder').classList.remove('hidden');
            state.currentPreviewData = null;
        }

    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'error');
    }
}

// Workflow Editor Logic
async function loadWorkflowTemplate() {
    try {
        const res = await fetch('/api/workflow');
        const data = await res.json();
        document.getElementById('workflow-editor').value = JSON.stringify(data.workflow, null, 2);
    } catch (e) {
        showToast('Failed to load workflow JSON', 'error');
    }
}

async function saveWorkflowTemplate() {
    const raw = document.getElementById('workflow-editor').value;
    try {
        const parsed = JSON.parse(raw);
        const res = await fetch('/api/workflow', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow: parsed })
        });
        if (res.ok) {
            showToast('Workflow template saved successfully', 'success');
        } else {
            throw new Error('Failed to save');
        }
    } catch (e) {
        showToast(`Invalid JSON: ${e.message}`, 'error');
    }
}

async function resetWorkflowTemplate() {
    if (!confirm('Reset workflow template to default?')) return;
    try {
        const res = await fetch('/api/workflow/reset', { method: 'POST' });
        const data = await res.json();
        document.getElementById('workflow-editor').value = JSON.stringify(data.workflow, null, 2);
        showToast('Reset to default workflow', 'info');
    } catch (e) {
        showToast('Failed to reset workflow', 'error');
    }
}

// Global Keyboard Shortcuts
document.addEventListener('keydown', (e) => {
    // Ctrl+Enter or Cmd+Enter to generate
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (state.activeTab === 'generator' && !state.isGenerating) {
            handleGenerate();
        }
    }
    // Escape to close modal
    if (e.key === 'Escape') {
        closeImageModal();
    }
});

// Initialization on DOM Ready
window.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    checkComfyStatus();
    loadModels();
    loadGallery(true);

    // Periodic status check
    setInterval(checkComfyStatus, 10000);
});
