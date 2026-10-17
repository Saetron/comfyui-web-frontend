// ComfyUI Studio Frontend Application Logic

// Global Application State
const state = {
    clientId: getOrCreateClientId(),
    ws: null,
    activeTab: 'generator',
    isGenerating: false,
    generationStartTime: null,
    generationTimerInterval: null,
    currentPreviewData: null,
    defaultNegativePrompt: '',

    // Workflows
    availableWorkflows: [],
    activeWorkflowId: 'default',
    activeWorkflowParams: null,

    // Gallery
    galleryImages: [],
    galleryOffset: 0,
    galleryLimit: 48,
    galleryTotal: 0,
    galleryLoading: false,
    searchTimeout: null,
    currentModalIndex: -1,
    currentModalImage: null,

    // TikTok Mode (Random)
    tiktokQueue: [],
    tiktokIndex: 0,
    currentTikTokImage: null,
    tiktokAutoplayInterval: null,
    tiktokAutoplayActive: false,

    // Delete Modal State
    imagePendingDelete: null,

    // Danbooru Tag Autocomplete
    topTags: [],
    autocompleteTargetInput: null,
    autocompleteActiveIndex: 0,
    autocompleteItems: [],
    autocompleteToken: '',
    autocompleteTokenStart: 0,
    autocompleteTokenEnd: 0,
    autocompleteDebounceTimer: null,

    // Tag Palettes Data
    tagPalettes: {
        quality: ['masterpiece', 'best quality', 'highly detailed', 'highres', 'absurdres', 'ultra-detailed', '8k wallpaper', 'sharp focus', 'intricate details'],
        subject: ['1girl', '1boy', '2girls', 'solo', 'multiple girls', 'portrait', 'upper body', 'cowboy shot', 'full body', 'looking at viewer', 'smile', 'blush'],
        style: ['anime screencap', 'official art', 'retro anime', 'illustration', 'watercolor', 'oil painting', 'chibi', 'lineart', 'monochrome'],
        lighting: ['cinematic lighting', 'volumetric lighting', 'god rays', 'bloom', 'soft lighting', 'sunset', 'dusk', 'backlighting', 'dramatic shadow'],
        negative: ['worst quality', 'low quality', 'bad anatomy', 'bad hands', 'missing fingers', 'extra digits', 'watermark', 'blurry', 'deformed', 'mutation', 'bad proportions']
    },
    activePalette: null,
    lastFocusedPromptInput: 'positive-prompt',
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
    if (!container) return;
    const toast = document.createElement('div');
    
    let borderGrad = 'border-pink-500/40 shadow-pink-950/30';
    let icon = '✨';
    let textGrad = 'text-pink-100';
    if (type === 'error') {
        borderGrad = 'border-rose-500/60 shadow-rose-950/50';
        icon = '⚠️';
        textGrad = 'text-rose-200';
    } else if (type === 'success') {
        borderGrad = 'border-emerald-400/50 shadow-emerald-950/50';
        icon = '🌸';
        textGrad = 'text-emerald-100';
    }

    toast.className = `pointer-events-auto flex items-center space-x-2.5 px-4 py-3 rounded-2xl border ${borderGrad} text-xs font-semibold shadow-2xl backdrop-blur-xl bg-[#0e0f1e]/95 toast-anime ${textGrad}`;
    toast.innerHTML = `<span class="text-sm shrink-0">${icon}</span><span class="leading-snug">${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px) scale(0.95)';
        setTimeout(() => toast.remove(), 320);
    }, 4200);
}

// Navigation & Tab Switching
function switchTab(tabId) {
    state.activeTab = tabId;
    ['generator', 'gallery', 'workflow'].forEach(tab => {
        const view = document.getElementById(`view-${tab}`);
        const btn = document.getElementById(`tab-${tab}`);
        if (tab === tabId) {
            view.classList.remove('hidden');
            btn.className = 'flex items-center space-x-2 px-3.5 sm:px-4 py-2 rounded-xl text-sm font-bold transition-all text-white anime-brand-pill shadow-md border border-pink-500/40';
        } else {
            view.classList.add('hidden');
            btn.className = 'flex items-center space-x-2 px-3.5 sm:px-4 py-2 rounded-xl text-sm font-medium transition-all text-zinc-400 hover:text-pink-200 hover:bg-white/5';
        }
    });

    if (tabId === 'gallery') {
        loadGallery(false);
    } else if (tabId === 'workflow') {
        loadWorkflowTemplate();
    }
}

// ==========================================
// WORKFLOW UPLOAD & DRAG-AND-DROP HANDLERS
// ==========================================
async function handleWorkflowFileInput(files) {
    if (!files || !files.length) return;
    const file = files[0];
    const formData = new FormData();
    formData.append('file', file);

    showToast(`(っ＾▿＾)っ 📂 Importing "${file.name}"...`, 'info');

    try {
        const res = await fetch('/api/comfy/workflows/upload', {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
            throw new Error(err.detail || 'Upload failed');
        }

        const data = await res.json();
        showToast(`(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧ ${data.message}`, 'success');

        // Reload workflows list and activate imported one
        if (data.workflows) {
            state.availableWorkflows = data.workflows;
        }
        await loadAvailableWorkflows();

        if (data.imported && data.imported.id) {
            await switchActiveWorkflow(data.imported.id);
        }

    } catch (e) {
        showToast(`(・_・;) ❌ Import failed: ${e.message}`, 'error');
    }
}

function handleWorkflowDragOver(e) {
    e.preventDefault();
    const dropzone = document.getElementById('workflow-dropzone');
    if (dropzone) dropzone.classList.add('dragover');
}

function handleWorkflowDragLeave(e) {
    e.preventDefault();
    const dropzone = document.getElementById('workflow-dropzone');
    if (dropzone) dropzone.classList.remove('dragover');
}

function handleWorkflowDrop(e) {
    e.preventDefault();
    const dropzone = document.getElementById('workflow-dropzone');
    if (dropzone) dropzone.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files) {
        handleWorkflowFileInput(e.dataTransfer.files);
    }
}

// ==========================================
// WORKFLOW PARAMETER SYNC & MANAGEMENT
// ==========================================
function applyWorkflowParamsToForm(params, sourceName = '') {
    if (!params || typeof params !== 'object') return;
    state.activeWorkflowParams = params;

    const updated = [];

    // 1. Checkpoint Model
    if (params.model) {
        const ckptSelect = document.getElementById('ckpt-select');
        if (ckptSelect) {
            let found = false;
            for (let i = 0; i < ckptSelect.options.length; i++) {
                if (ckptSelect.options[i].value === params.model) {
                    ckptSelect.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                const opt = document.createElement('option');
                opt.value = params.model;
                opt.textContent = `⭐ ${params.model} (From Workflow)`;
                opt.selected = true;
                ckptSelect.appendChild(opt);
            }
            updated.push(`Model: ${params.model}`);
        }
    }

    // 2. Steps
    if (params.steps !== undefined && params.steps !== null) {
        const stepsSlider = document.getElementById('steps-slider');
        const stepsVal = document.getElementById('steps-val');
        if (stepsSlider) stepsSlider.value = params.steps;
        if (stepsVal) stepsVal.innerText = params.steps;
        updated.push(`Steps: ${params.steps}`);
    }

    // 3. CFG Scale
    if (params.cfg !== undefined && params.cfg !== null) {
        const cfgSlider = document.getElementById('cfg-slider');
        const cfgVal = document.getElementById('cfg-val');
        if (cfgSlider) cfgSlider.value = params.cfg;
        if (cfgVal) cfgVal.innerText = params.cfg;
        updated.push(`CFG: ${params.cfg}`);
    }

    // 4. Canvas Resolution (Width & Height)
    if (params.width && params.height) {
        setResolution(params.width, params.height);
        updated.push(`Size: ${params.width}×${params.height}`);
    } else {
        if (params.width) {
            const wInput = document.getElementById('width-input');
            if (wInput) wInput.value = params.width;
        }
        if (params.height) {
            const hInput = document.getElementById('height-input');
            if (hInput) hInput.value = params.height;
        }
    }

    // 5. Sampler
    if (params.sampler_name) {
        const samplerSelect = document.getElementById('sampler-select');
        if (samplerSelect) {
            let found = false;
            for (let i = 0; i < samplerSelect.options.length; i++) {
                if (samplerSelect.options[i].value.toLowerCase() === params.sampler_name.toLowerCase()) {
                    samplerSelect.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                const opt = document.createElement('option');
                opt.value = params.sampler_name;
                opt.textContent = params.sampler_name;
                opt.selected = true;
                samplerSelect.appendChild(opt);
            }
            updated.push(`Sampler: ${params.sampler_name}`);
        }
    }

    // 6. Scheduler
    if (params.scheduler) {
        const schedSelect = document.getElementById('scheduler-select');
        if (schedSelect) {
            let found = false;
            for (let i = 0; i < schedSelect.options.length; i++) {
                if (schedSelect.options[i].value.toLowerCase() === params.scheduler.toLowerCase()) {
                    schedSelect.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                const opt = document.createElement('option');
                opt.value = params.scheduler;
                opt.textContent = params.scheduler;
                opt.selected = true;
                schedSelect.appendChild(opt);
            }
            updated.push(`Scheduler: ${params.scheduler}`);
        }
    }

    // 7. Seed
    if (params.seed !== undefined && params.seed !== null && params.seed > 0) {
        const seedInput = document.getElementById('seed-input');
        if (seedInput) seedInput.value = params.seed;
    }

    // 8. Positive & Negative Prompts (populate if empty)
    const posInput = document.getElementById('positive-prompt');
    if (posInput && params.positive_prompt && !posInput.value.trim()) {
        posInput.value = params.positive_prompt;
    }
    const negInput = document.getElementById('negative-prompt');
    if (negInput && params.negative_prompt && !negInput.value.trim()) {
        negInput.value = params.negative_prompt;
    }

    if (sourceName && updated.length > 0) {
        showToast(`🎛️ Synced tuning from "${sourceName}"!`, 'info');
    }
}

async function loadActiveWorkflowTemplate() {
    try {
        const res = await fetch('/api/workflow');
        if (!res.ok) return;
        const data = await res.json();
        if (data.parsed_params) {
            applyWorkflowParamsToForm(data.parsed_params);
        }
        if (data.active_id) {
            state.activeWorkflowId = data.active_id;
            const s1 = document.getElementById('active-workflow-select');
            const s2 = document.getElementById('editor-workflow-select');
            if (s1) s1.value = data.active_id;
            if (s2) s2.value = data.active_id;
        }
    } catch (e) {
        console.warn('Failed to load active workflow template on startup:', e);
    }
}

async function loadAvailableWorkflows() {
    try {
        const res = await fetch('/api/comfy/workflows');
        if (!res.ok) return;
        const workflows = await res.json();
        state.availableWorkflows = workflows;

        const headerSelect = document.getElementById('active-workflow-select');
        const editorSelect = document.getElementById('editor-workflow-select');
        
        [headerSelect, editorSelect].forEach(select => {
            if (!select) return;
            select.innerHTML = '';
            
            const defaultGroup = document.createElement('optgroup');
            defaultGroup.label = 'Default Templates';
            const serverGroup = document.createElement('optgroup');
            serverGroup.label = 'ComfyUI Server Workflows';
            const localGroup = document.createElement('optgroup');
            localGroup.label = 'Local Workflows';

            workflows.forEach(wf => {
                const opt = document.createElement('option');
                opt.value = wf.id;
                opt.textContent = wf.name;
                if (wf.is_active) {
                    opt.selected = true;
                    state.activeWorkflowId = wf.id;
                }

                if (wf.source === 'default') defaultGroup.appendChild(opt);
                else if (wf.source === 'server') serverGroup.appendChild(opt);
                else localGroup.appendChild(opt);
            });

            if (defaultGroup.children.length) select.appendChild(defaultGroup);
            if (serverGroup.children.length) select.appendChild(serverGroup);
            if (localGroup.children.length) select.appendChild(localGroup);
        });

        const activePill = document.getElementById('active-workflow-pill');
        if (activePill) {
            const currentWf = workflows.find(w => w.id === state.activeWorkflowId);
            activePill.innerText = currentWf ? currentWf.name : 'Active';
        }

        // Also sync the active workflow template and its tuning parameters to the Generator
        await loadActiveWorkflowTemplate();

    } catch (e) {
        console.warn('Failed to load available workflows:', e);
    }
}

async function switchActiveWorkflow(workflowId) {
    try {
        const res = await fetch('/api/comfy/workflows/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow_id: workflowId })
        });
        if (!res.ok) throw new Error('Activation failed');
        const data = await res.json();
        state.activeWorkflowId = workflowId;

        // Sync dropdowns
        const s1 = document.getElementById('active-workflow-select');
        const s2 = document.getElementById('editor-workflow-select');
        if (s1) s1.value = workflowId;
        if (s2) s2.value = workflowId;

        // Update editor if in workflow view
        const editor = document.getElementById('workflow-editor');
        if (editor && data.workflow) {
            editor.value = JSON.stringify(data.workflow, null, 2);
        }

        const activePill = document.getElementById('active-workflow-pill');
        const currentWf = state.availableWorkflows.find(w => w.id === workflowId);
        if (activePill && currentWf) {
            activePill.innerText = currentWf.name;
        }

        // Apply workflow parameters directly to generator controls (Anime Tuning)
        if (data.parsed_params) {
            applyWorkflowParamsToForm(data.parsed_params, currentWf ? currentWf.name : workflowId);
        }

        showToast(`Activated workflow: ${currentWf ? currentWf.name : workflowId} ✨`, 'success');
    } catch (e) {
        showToast(`Failed to switch workflow: ${e.message}`, 'error');
    }
}

async function saveWorkflowToServerOrLocal(destination = 'local') {
    const raw = document.getElementById('workflow-editor').value;
    try {
        const parsed = JSON.parse(raw);
        const promptMsg = destination === 'server'
            ? 'Enter filename to save on ComfyUI server (e.g. my_workflow.json):'
            : 'Enter filename to save locally (e.g. custom_sdxl.json):';
        const name = prompt(promptMsg, 'my_workflow.json');
        if (!name || !name.trim()) return;

        const res = await fetch('/api/comfy/workflows/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: name.trim(),
                workflow: parsed,
                destination: destination
            })
        });

        if (!res.ok) throw new Error('Save failed');
        showToast(`Saved workflow to ${destination}: ${name}`, 'success');
        await loadAvailableWorkflows();
    } catch (e) {
        showToast(`Error saving workflow: ${e.message}`, 'error');
    }
}

// ==========================================
// CONFIGURATION & STATUS
// ==========================================
async function checkComfyStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const badge = document.getElementById('comfy-status-badge');
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('status-text');

        if (data.default_negative_prompt) {
            state.defaultNegativePrompt = data.default_negative_prompt;
            const negInput = document.getElementById('negative-prompt');
            if (negInput && !negInput.value.trim()) {
                negInput.value = state.defaultNegativePrompt;
            }
        }

        if (data.comfyui && data.comfyui.connected) {
            dot.className = 'w-2 h-2 rounded-full bg-emerald-400';
            text.innerText = 'ComfyUI Online';
            badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium bg-emerald-950/60 border border-emerald-800/80 text-emerald-300';
        } else {
            dot.className = 'w-2 h-2 rounded-full bg-rose-500';
            text.innerText = 'ComfyUI Offline';
            badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium bg-rose-950/60 border border-rose-800/80 text-rose-300';
        }

        if (data.total_images !== undefined) {
            document.getElementById('gallery-count-badge').innerText = data.total_images;
        }
    } catch (e) {
        document.getElementById('status-dot').className = 'w-2 h-2 rounded-full bg-rose-500';
        document.getElementById('status-text').innerText = 'Backend Offline';
    }
}

function resetToDefaultNegativePrompt() {
    const neg = document.getElementById('negative-prompt');
    neg.value = state.defaultNegativePrompt || 'worst quality, low quality, bad anatomy, bad hands, missing fingers, extra digits, signature, watermark, blurry, deformed';
    showToast('Reset negative prompt to default', 'info');
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
        // If an active workflow specified a model, ensure it's selected
        if (state.activeWorkflowParams && state.activeWorkflowParams.model) {
            const targetModel = state.activeWorkflowParams.model;
            let found = false;
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === targetModel) {
                    select.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                const opt = document.createElement('option');
                opt.value = targetModel;
                opt.textContent = `⭐ ${targetModel} (From Workflow)`;
                opt.selected = true;
                select.appendChild(opt);
            }
        }
    } catch (e) {
        console.warn('Could not load models list:', e);
    }
}

// ==========================================
// DANBOORU TAG AUTOCOMPLETE & PALETTES
// ==========================================
async function preloadTopTags() {
    try {
        const res = await fetch('/api/tags/top?limit=1500');
        if (res.ok) {
            state.topTags = await res.json();
        }
    } catch (e) {
        console.warn('Could not preload top tags:', e);
    }
}

function initTagAutocomplete() {
    const inputs = ['positive-prompt', 'negative-prompt'];
    inputs.forEach(id => {
        const elem = document.getElementById(id);
        if (!elem) return;

        elem.addEventListener('focus', () => {
            state.lastFocusedPromptInput = id;
        });

        elem.addEventListener('input', (e) => {
            state.lastFocusedPromptInput = id;
            handlePromptInput(elem);
        });

        elem.addEventListener('keydown', (e) => {
            handlePromptKeydown(elem, e);
        });

        elem.addEventListener('blur', () => {
            // Slight delay so clicks on dropdown items register before hide
            setTimeout(hideAutocomplete, 250);
        });
    });

    // Close autocomplete on click outside
    document.addEventListener('click', (e) => {
        const dd = document.getElementById('tag-autocomplete');
        if (!dd.contains(e.target) && !inputs.some(id => document.getElementById(id) === e.target)) {
            hideAutocomplete();
        }
    });
}

function handlePromptInput(textarea) {
    const cursor = textarea.selectionStart;
    const text = textarea.value;

    // Find the boundary of the comma-separated tag around cursor
    const beforeCursor = text.substring(0, cursor);
    const afterCursor = text.substring(cursor);

    const lastCommaBefore = beforeCursor.lastIndexOf(',');
    const nextCommaAfter = afterCursor.indexOf(',');

    const tokenStart = lastCommaBefore === -1 ? 0 : lastCommaBefore + 1;
    const tokenEnd = nextCommaAfter === -1 ? text.length : cursor + nextCommaAfter;

    let rawToken = text.substring(tokenStart, cursor).trimStart();
    // Clean weighting symbols from query (e.g. (1girl or {1girl
    let cleanToken = rawToken.replace(/^[({[\s]+/, '').trim();

    state.autocompleteToken = cleanToken;
    state.autocompleteTokenStart = tokenStart;
    state.autocompleteTokenEnd = tokenEnd;
    state.autocompleteTargetInput = textarea;

    if (cleanToken.length < 1) {
        hideAutocomplete();
        return;
    }

    // Debounce search
    clearTimeout(state.autocompleteDebounceTimer);
    state.autocompleteDebounceTimer = setTimeout(() => {
        searchAndShowAutocomplete(cleanToken, textarea);
    }, 80);
}

async function searchAndShowAutocomplete(query, textarea) {
    const qLower = query.toLowerCase().replace(/[\s_]+/g, '_');
    let matches = [];

    // 1. Check preloaded top tags first
    if (state.topTags.length > 0) {
        matches = state.topTags.filter(t => 
            t.name.startsWith(qLower) || 
            t.name.replace(/_/g, ' ').startsWith(query.toLowerCase()) ||
            t.name.includes(qLower)
        ).slice(0, 15);
    }

    // 2. Fall back to backend search if few matches
    if (matches.length < 5 && query.length >= 2) {
        try {
            const res = await fetch(`/api/tags?q=${encodeURIComponent(query)}&limit=20`);
            if (res.ok) {
                matches = await res.json();
            }
        } catch (e) {
            console.warn('API tag search error:', e);
        }
    }

    if (matches.length === 0) {
        hideAutocomplete();
        return;
    }

    state.autocompleteItems = matches;
    state.autocompleteActiveIndex = 0;
    renderAutocompleteDropdown(matches, textarea);
}

function renderAutocompleteDropdown(items, textarea) {
    const dd = document.getElementById('tag-autocomplete');
    dd.innerHTML = '';

    const rect = textarea.getBoundingClientRect();
    dd.style.top = `${rect.bottom + window.scrollY + 4}px`;
    dd.style.left = `${rect.left + window.scrollX}px`;
    dd.style.width = `${Math.min(rect.width, 420)}px`;

    items.forEach((item, idx) => {
        const row = document.createElement('div');
        row.className = `tag-autocomplete-item ${idx === 0 ? 'active' : ''}`;
        
        const catClass = `tag-cat-${item.category_name || 'general'}`;
        
        row.innerHTML = `
            <div class="flex items-center space-x-2 truncate">
                <span class="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${catClass}">
                    ${item.category_name || 'general'}
                </span>
                <span class="font-medium text-zinc-100 truncate">${item.name}</span>
                ${item.aliases ? `<span class="text-[10px] text-zinc-500 italic truncate">(${item.aliases.split(',')[0]})</span>` : ''}
            </div>
            <span class="text-[11px] font-mono text-zinc-400 shrink-0 ml-2">${item.count_formatted || item.count}</span>
        `;

        row.onmousedown = (e) => {
            e.preventDefault();
            selectAutocompleteItem(item);
        };
        dd.appendChild(row);
    });

    dd.classList.remove('hidden');
}

function hideAutocomplete() {
    const dd = document.getElementById('tag-autocomplete');
    if (dd) dd.classList.add('hidden');
    state.autocompleteItems = [];
}

function selectAutocompleteItem(item) {
    const textarea = state.autocompleteTargetInput;
    if (!textarea) return;

    const text = textarea.value;
    const cursor = textarea.selectionStart;

    const beforeToken = text.substring(0, state.autocompleteTokenStart);
    const afterToken = text.substring(state.autocompleteTokenEnd);

    // Check if user had a leading weight paren like (tag
    const prePart = text.substring(state.autocompleteTokenStart, cursor);
    const leadingBrackets = prePart.match(/^[({[\s]*/)?.[0] || '';

    // Leading space if after a comma
    const leadingSpace = beforeToken.endsWith(',') && !beforeToken.endsWith(', ') ? ' ' : '';
    
    // Insert tag with comma and space
    const insertStr = `${leadingSpace}${leadingBrackets}${item.name}, `;
    textarea.value = beforeToken + insertStr + afterToken.replace(/^[\s,]*/, '');

    const newCursor = beforeToken.length + insertStr.length;
    textarea.setSelectionRange(newCursor, newCursor);
    textarea.focus();

    hideAutocomplete();
}

function handlePromptKeydown(textarea, e) {
    const dd = document.getElementById('tag-autocomplete');
    const isVisible = dd && !dd.classList.contains('hidden');

    // 1. Tag weight adjustment with Ctrl+Up / Ctrl+Down
    if ((e.ctrlKey || e.metaKey) && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        adjustTagWeightAtCursor(textarea, e.key === 'ArrowUp' ? 0.05 : -0.05);
        return;
    }

    // 2. Autocomplete navigation
    if (isVisible && state.autocompleteItems.length > 0) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            state.autocompleteActiveIndex = (state.autocompleteActiveIndex + 1) % state.autocompleteItems.length;
            updateAutocompleteActiveHighlight();
            return;
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            state.autocompleteActiveIndex = (state.autocompleteActiveIndex - 1 + state.autocompleteItems.length) % state.autocompleteItems.length;
            updateAutocompleteActiveHighlight();
            return;
        } else if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            const selected = state.autocompleteItems[state.autocompleteActiveIndex];
            if (selected) selectAutocompleteItem(selected);
            return;
        } else if (e.key === 'Escape') {
            e.preventDefault();
            hideAutocomplete();
            return;
        }
    }
}

function updateAutocompleteActiveHighlight() {
    const dd = document.getElementById('tag-autocomplete');
    const children = dd.querySelectorAll('.tag-autocomplete-item');
    children.forEach((child, idx) => {
        if (idx === state.autocompleteActiveIndex) {
            child.classList.add('active');
            child.scrollIntoView({ block: 'nearest' });
        } else {
            child.classList.remove('active');
        }
    });
}

// Adjust weight like WebUI: (tag:1.1)
function adjustTagWeightAtCursor(textarea, delta) {
    const text = textarea.value;
    const selStart = textarea.selectionStart;
    const selEnd = textarea.selectionEnd;

    let targetRange = [selStart, selEnd];

    // If no text selected, select the tag around cursor
    if (selStart === selEnd) {
        const lastComma = text.substring(0, selStart).lastIndexOf(',');
        const nextComma = text.indexOf(',', selStart);
        const start = lastComma === -1 ? 0 : lastComma + 1;
        const end = nextComma === -1 ? text.length : nextComma;
        targetRange = [start, end];
    }

    let chunk = text.substring(targetRange[0], targetRange[1]);
    const leadingWhitespace = chunk.match(/^\s*/)?.[0] || '';
    const trailingWhitespace = chunk.match(/\s*$/)?.[0] || '';
    chunk = chunk.trim();

    if (!chunk) return;

    let newChunk = chunk;
    const weightMatch = chunk.match(/^\((.+):([0-9.]+)\)$/);

    if (weightMatch) {
        const tagName = weightMatch[1];
        let currentWeight = parseFloat(weightMatch[2]);
        let newWeight = Math.round((currentWeight + delta) * 100) / 100;
        if (Math.abs(newWeight - 1.0) < 0.01) {
            newChunk = tagName;
        } else if (newWeight <= 0.1) {
            newChunk = `(${tagName}:0.1)`;
        } else {
            newChunk = `(${tagName}:${newWeight.toFixed(2)})`;
        }
    } else {
        const parenMatch = chunk.match(/^\((.+)\)$/);
        if (parenMatch) {
            const tagName = parenMatch[1];
            let newWeight = Math.round((1.1 + delta) * 100) / 100;
            newChunk = `(${tagName}:${newWeight.toFixed(2)})`;
        } else {
            let newWeight = Math.round((1.0 + delta) * 100) / 100;
            if (newWeight !== 1.0) {
                newChunk = `(${chunk}:${newWeight.toFixed(2)})`;
            }
        }
    }

    const replacement = leadingWhitespace + newChunk + trailingWhitespace;
    textarea.value = text.substring(0, targetRange[0]) + replacement + text.substring(targetRange[1]);
    textarea.setSelectionRange(targetRange[0] + leadingWhitespace.length, targetRange[0] + replacement.length - trailingWhitespace.length);
}

// Tag Palettes Drawer
function toggleTagPalette(category) {
    const drawer = document.getElementById('tag-pills-drawer');
    if (state.activePalette === category && !drawer.classList.contains('hidden')) {
        drawer.classList.add('hidden');
        state.activePalette = null;
        return;
    }

    state.activePalette = category;
    const tags = state.tagPalettes[category] || [];
    drawer.innerHTML = '';

    tags.forEach(t => {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'tag-pill-btn bg-white/5 hover:bg-pink-500/20 text-pink-200 border border-pink-500/30 text-[11px]';
        pill.innerHTML = `<span class="text-pink-400 font-bold">+</span><span>${t}</span>`;
        pill.onclick = () => insertTagIntoActivePrompt(t);
        drawer.appendChild(pill);
    });

    drawer.classList.remove('hidden');
}

function insertTagIntoActivePrompt(tag) {
    const targetId = state.activePalette === 'negative' ? 'negative-prompt' : (state.lastFocusedPromptInput || 'positive-prompt');
    const input = document.getElementById(targetId);
    if (!input) return;

    let val = input.value.trim();
    if (val.length > 0 && !val.endsWith(',')) {
        val += ', ';
    } else if (val.endsWith(',')) {
        val += ' ';
    }
    input.value = val + tag + ', ';
    input.focus();
    showToast(`Added: ${tag}`, 'info');
}

function convertActivePromptSpacesToUnderscores() {
    const input = document.getElementById(state.lastFocusedPromptInput || 'positive-prompt');
    if (!input) return;
    input.value = input.value.replace(/\s+/g, '_').replace(/,_+/g, ', ');
    showToast('Converted spaces to underscores', 'info');
}

function convertActivePromptUnderscoresToSpaces() {
    const input = document.getElementById(state.lastFocusedPromptInput || 'positive-prompt');
    if (!input) return;
    input.value = input.value.replace(/_+/g, ' ');
    showToast('Converted underscores to spaces', 'info');
}

function cleanPromptTags() {
    const input = document.getElementById(state.lastFocusedPromptInput || 'positive-prompt');
    if (!input) return;
    const parts = input.value.split(',').map(s => s.trim()).filter(Boolean);
    const seen = new Set();
    const unique = [];
    parts.forEach(p => {
        const lower = p.toLowerCase();
        if (!seen.has(lower)) {
            seen.add(lower);
            unique.push(p);
        }
    });
    input.value = unique.join(', ') + (unique.length > 0 ? ', ' : '');
    showToast(`Cleaned and deduplicated (${unique.length} tags)`, 'success');
}

// ==========================================
// WEBSOCKET MANAGEMENT
// ==========================================
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

    setInterval(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);
}

function handleWsMessage(msg) {
    if (msg.type === 'gallery_item_added') {
        onGalleryItemAdded(msg.item);
    } else if (msg.type === 'gallery_item_deleted') {
        onGalleryItemDeleted(msg.relative_path);
    }

    if (msg.channel === 'generation') {
        const data = msg.data || {};
        const params = msg.resolved_params || {};

        const nodeLabel = document.getElementById('preview-progress-text') || document.getElementById('progress-node-label');
        const statusHeading = document.getElementById('preview-status-heading') || document.getElementById('preview-status-pill');
        const progressBar = document.getElementById('preview-progress-bar') || document.getElementById('progress-bar');
        const progressPct = document.getElementById('preview-progress-pct') || document.getElementById('progress-percentage');

        if (data.type === 'status') {
            const queue = data.queue_remaining;
            if (nodeLabel) nodeLabel.innerText = queue > 0 ? `In Queue (position: ${queue})...` : 'Starting generation...';
            if (statusHeading) statusHeading.innerText = 'Queued';
        } else if (data.type === 'execution_start') {
            if (nodeLabel) nodeLabel.innerText = 'Initializing nodes...';
            if (statusHeading) statusHeading.innerText = 'Running';
        } else if (data.type === 'executing') {
            if (nodeLabel) nodeLabel.innerText = `Executing node #${data.node}...`;
        } else if (data.type === 'progress') {
            const pct = data.percentage || 0;
            if (progressBar) progressBar.style.width = `${pct}%`;
            if (progressPct) progressPct.innerText = `${pct}%`;
            if (nodeLabel) nodeLabel.innerText = `Sampling: step ${data.value}/${data.max} (${pct}%)`;
            if (statusHeading) statusHeading.innerText = `Step ${data.value}/${data.max}`;
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
            if (statusHeading) statusHeading.innerText = 'Error';
            if (nodeLabel) nodeLabel.innerText = `Error: ${data.error}`;
        }
    }
}

// ==========================================
// GENERATOR FORM LOGIC
// ==========================================
function toggleSeedInput() {
    const isRandom = document.getElementById('seed-randomize').checked;
    const input = document.getElementById('seed-input');
    input.disabled = isRandom;
    if (isRandom) input.classList.add('opacity-50');
    else input.classList.remove('opacity-50');
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

    const positivePromptEl = document.getElementById('positive-prompt');
    const positivePrompt = positivePromptEl ? positivePromptEl.value.trim() : '';
    const negativePromptEl = document.getElementById('negative-prompt');
    const negativePrompt = negativePromptEl ? negativePromptEl.value.trim() : '';

    const seedRandomizeEl = document.getElementById('seed-randomize');
    const isRandomSeed = seedRandomizeEl ? seedRandomizeEl.checked : true;
    const seedInputEl = document.getElementById('seed-input');
    const rawSeed = seedInputEl ? seedInputEl.value : '';
    const seed = isRandomSeed ? null : parseInt(rawSeed, 10);

    if (!positivePrompt) {
        showToast('(・_・;) Please enter a positive prompt', 'error');
        if (positivePromptEl) positivePromptEl.focus();
        return;
    }

    const stepsEl = document.getElementById('steps-slider');
    const cfgEl = document.getElementById('cfg-slider');
    const widthEl = document.getElementById('width-input');
    const heightEl = document.getElementById('height-input');
    const ckptEl = document.getElementById('ckpt-select');
    const samplerEl = document.getElementById('sampler-select');
    const schedEl = document.getElementById('scheduler-select');

    const payload = {
        positive_prompt: positivePrompt,
        negative_prompt: negativePrompt,
        seed: seed,
        randomize_seed: isRandomSeed,
        client_id: state.clientId,
        steps: stepsEl ? parseInt(stepsEl.value, 10) : 20,
        cfg: cfgEl ? parseFloat(cfgEl.value) : 7.5,
        width: widthEl ? (parseInt(widthEl.value, 10) || null) : null,
        height: heightEl ? (parseInt(heightEl.value, 10) || null) : null,
        ckpt_name: (ckptEl && ckptEl.value) ? ckptEl.value : null,
        sampler_name: (samplerEl && samplerEl.value) ? samplerEl.value : null,
        scheduler: (schedEl && schedEl.value) ? schedEl.value : null,
    };

    state.isGenerating = true;
    startGenerationTimer();

    const btnGen = document.getElementById('btn-generate');
    const btnInterrupt = document.getElementById('btn-interrupt');
    const genText = document.getElementById('generate-text');
    if (btnGen) btnGen.disabled = true;
    if (genText) genText.innerText = 'Queuing...';
    if (btnInterrupt) btnInterrupt.classList.remove('hidden');

    const progressContainer = document.getElementById('preview-progress-container') || document.getElementById('progress-container');
    const progressBar = document.getElementById('preview-progress-bar') || document.getElementById('progress-bar');
    const progressPct = document.getElementById('preview-progress-pct') || document.getElementById('progress-percentage');
    const progressLabel = document.getElementById('preview-progress-text') || document.getElementById('progress-node-label');
    const statusHeading = document.getElementById('preview-status-heading') || document.getElementById('preview-status-pill');

    if (progressContainer) progressContainer.classList.remove('hidden');
    if (progressBar) progressBar.style.width = '0%';
    if (progressPct) progressPct.innerText = '0%';
    if (progressLabel) progressLabel.innerText = 'Contacting ComfyUI...';
    if (statusHeading) statusHeading.innerText = 'Queuing';

    try {
        const res = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Failed to submit generation' }));
            throw new Error(err.detail || 'Failed to submit generation');
        }

        const data = await res.json();
        if (genText) genText.innerText = 'Generating...';

        if (data.resolved_params && data.resolved_params.seed && seedInputEl) {
            seedInputEl.value = data.resolved_params.seed;
        }
    } catch (e) {
        finishGeneration();
        showToast(e.message, 'error');
        if (statusHeading) statusHeading.innerText = 'Failed';
        if (progressLabel) progressLabel.innerText = `Error: ${e.message}`;
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
    const timerElem = document.getElementById('preview-timer') || document.getElementById('gen-timer');
    if (timerElem) timerElem.innerText = '0.0s';
    if (state.generationTimerInterval) clearInterval(state.generationTimerInterval);
    state.generationTimerInterval = setInterval(() => {
        const elapsed = ((Date.now() - state.generationStartTime) / 1000).toFixed(1);
        const t = document.getElementById('preview-timer') || document.getElementById('gen-timer');
        if (t) t.innerText = `${elapsed}s`;
    }, 100);
}

function finishGeneration() {
    state.isGenerating = false;
    if (state.generationTimerInterval) clearInterval(state.generationTimerInterval);

    const btnGen = document.getElementById('btn-generate');
    const btnInterrupt = document.getElementById('btn-interrupt');
    const genText = document.getElementById('generate-text');

    if (btnGen) btnGen.disabled = false;
    if (genText) genText.innerText = '(★ω★) 🚀 Spark Generation!';
    if (btnInterrupt) btnInterrupt.classList.add('hidden');
}

function displayGeneratedImage(relative_path, params = {}) {
    state.currentPreviewData = { relative_path, params };

    const imgUrl = `/api/images/${encodeURIComponent(cleanRelPath(relative_path))}`;
    const previewImg = document.getElementById('preview-img');
    const placeholder = document.getElementById('preview-placeholder');
    const wrapper = document.getElementById('preview-image-wrapper');
    const footer = document.getElementById('preview-meta-footer');

    if (previewImg) previewImg.src = imgUrl;
    if (placeholder) placeholder.classList.add('hidden');
    if (wrapper) wrapper.classList.remove('hidden');
    if (footer) footer.classList.remove('hidden');

    const statusHeading = document.getElementById('preview-status-heading') || document.getElementById('preview-status-pill');
    const progressBar = document.getElementById('preview-progress-bar') || document.getElementById('progress-bar');
    const progressPct = document.getElementById('preview-progress-pct') || document.getElementById('progress-percentage');
    const progressLabel = document.getElementById('preview-progress-text') || document.getElementById('progress-node-label');

    if (statusHeading) statusHeading.innerText = 'Canvas Preview (プレビュー)';
    if (progressBar) progressBar.style.width = '100%';
    if (progressPct) progressPct.innerText = '100%';
    if (progressLabel) progressLabel.innerText = 'Generation complete!';

    const seedBadge = document.getElementById('preview-seed-badge');
    if (seedBadge && params.seed) {
        seedBadge.innerText = `Seed: ${params.seed}`;
    }
    const fnBadge = document.getElementById('preview-filename-badge');
    if (fnBadge) fnBadge.innerText = relative_path;

    showToast('(ﾉ◕ヮ◕)ﾉ*:･ﾟ Image generated successfully! ✨', 'success');
}

function openCurrentInModal() {
    if (state.currentPreviewData && state.currentPreviewData.relative_path) {
        openImageModalByPath(state.currentPreviewData.relative_path);
    }
}

function downloadCurrentPreview() {
    if (state.currentPreviewData && state.currentPreviewData.relative_path) {
        const a = document.createElement('a');
        a.href = `/api/images/${encodeURIComponent(cleanRelPath(state.currentPreviewData.relative_path))}`;
        a.download = state.currentPreviewData.relative_path.split('/').pop();
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
}

// ==========================================
// GALLERY VIEW LOGIC
// ==========================================
function cleanRelPath(p) {
    if (!p) return '';
    let s = p.replace(/\\/g, '/');
    if (s.startsWith('/app/output/')) s = s.substring('/app/output/'.length);
    else if (s.startsWith('/app/output')) s = s.substring('/app/output'.length);
    return s.replace(/^\/+/, '');
}

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

        const loadMoreContainer = document.getElementById('gallery-load-more-container');
        if (state.galleryImages.length < data.total) {
            loadMoreContainer.classList.remove('hidden');
        } else {
            loadMoreContainer.classList.add('hidden');
        }

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

    state.galleryImages.forEach((item, index) => {
        const card = createGalleryCard(item, index);
        grid.appendChild(card);
    });
}

function createGalleryCard(item, index) {
    const card = document.createElement('div');
    const clean = cleanRelPath(item.relative_path);
    card.id = `gallery-card-${sanitizeId(clean)}`;
    card.className = 'group relative aspect-square bg-[#0c0d1b] rounded-2xl overflow-hidden border-2 border-pink-500/25 hover:border-pink-400 hover:shadow-xl hover:shadow-pink-500/25 transition-all duration-200 cursor-pointer hover:-translate-y-1';

    const thumbUrl = `/api/thumbnails/${encodeURIComponent(clean)}`;

    card.innerHTML = `
        <img src="${thumbUrl}" alt="${item.filename}" loading="lazy" class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105">
        
        <div class="absolute inset-0 bg-gradient-to-t from-black/90 via-black/35 to-transparent opacity-0 group-hover:opacity-100 transition-opacity p-3 flex flex-col justify-end">
            <span class="text-xs font-bold text-pink-100 truncate drop-shadow">${item.filename}</span>
            <div class="flex items-center justify-between text-[10px] text-pink-300 mt-1">
                <span class="font-mono">${item.file_size_human}</span>
                <span class="px-2 py-0.5 rounded-full bg-pink-500/30 text-white font-bold border border-pink-500/40">🌸 View</span>
            </div>
        </div>
    `;

    card.onclick = () => openImageModalByIndex(index);
    return card;
}

function sanitizeId(path) {
    return path.replace(/[^a-zA-Z0-9-_]/g, '_');
}

function onGalleryItemAdded(item) {
    const badge = document.getElementById('gallery-count-badge');
    let count = parseInt(badge.innerText, 10) || 0;
    badge.innerText = count + 1;

    const clean = cleanRelPath(item.relative_path);
    const existingIndex = state.galleryImages.findIndex(i => cleanRelPath(i.relative_path) === clean);
    if (existingIndex === -1) {
        state.galleryImages.unshift(item);
        const grid = document.getElementById('gallery-grid');
        const empty = document.getElementById('gallery-empty');
        if (empty) empty.classList.add('hidden');

        const card = createGalleryCard(item, 0);
        grid.insertBefore(card, grid.firstChild);
    }
}

function onGalleryItemDeleted(relative_path) {
    const clean = cleanRelPath(relative_path);
    const badge = document.getElementById('gallery-count-badge');
    let count = parseInt(badge.innerText, 10) || 0;
    if (count > 0) badge.innerText = count - 1;

    state.galleryImages = state.galleryImages.filter(i => cleanRelPath(i.relative_path) !== clean);

    const card = document.getElementById(`gallery-card-${sanitizeId(clean)}`);
    if (card) card.remove();

    if (state.galleryImages.length === 0) {
        const empty = document.getElementById('gallery-empty');
        if (empty) empty.classList.remove('hidden');
    }
}

// ==========================================
// IMAGE DETAIL MODAL WITH PREV / NEXT NAV
// ==========================================
function openImageModalByPath(relativePath) {
    const clean = cleanRelPath(relativePath);
    let idx = state.galleryImages.findIndex(i => cleanRelPath(i.relative_path) === clean);
    if (idx === -1) idx = 0;
    openImageModalByIndex(idx, clean);
}

async function openImageModalByIndex(index, fallbackPath = null) {
    const modal = document.getElementById('image-modal');
    if (!modal) return;
    modal.classList.remove('hidden');

    if (state.galleryImages && state.galleryImages.length > 0) {
        if (index < 0) index = state.galleryImages.length - 1;
        if (index >= state.galleryImages.length) index = 0;
        state.currentModalIndex = index;
    }

    const item = (state.galleryImages && state.galleryImages[state.currentModalIndex]) || {};
    const relativePath = cleanRelPath(item.relative_path || fallbackPath);

    const navInd = document.getElementById('modal-nav-indicator') || document.getElementById('modal-img-counter');
    if (navInd) {
        navInd.innerText = (state.galleryImages && state.galleryImages.length > 0)
            ? `${state.currentModalIndex + 1} / ${state.galleryImages.length}`
            : '1 / 1';
    }

    const modalImg = document.getElementById('modal-img');
    const modalFilename = document.getElementById('modal-filename');
    const modalDate = document.getElementById('modal-date') || document.getElementById('modal-filesize');
    const modalDim = document.getElementById('modal-img-dim');
    const modalDownload = document.getElementById('modal-download-link');

    const cleanFilename = relativePath.split('/').pop() || item.filename || 'image.png';

    if (modalFilename) modalFilename.innerText = cleanFilename;
    if (modalDate) modalDate.innerText = 'Loading metadata...';
    if (modalDim) modalDim.innerText = '';

    const posTagsEl = document.getElementById('modal-pos-tags');
    const posPromptEl = document.getElementById('modal-pos-prompt') || document.getElementById('modal-positive-prompt');
    const negPromptEl = document.getElementById('modal-neg-prompt') || document.getElementById('modal-negative-prompt');

    if (posTagsEl) posTagsEl.innerHTML = '';
    if (posPromptEl) posPromptEl.innerText = 'Loading...';
    if (negPromptEl) negPromptEl.innerText = 'Loading...';

    const seedEl = document.getElementById('modal-seed');
    const stepsEl = document.getElementById('modal-steps');
    const cfgEl = document.getElementById('modal-cfg');
    const samplerEl = document.getElementById('modal-sampler');
    const modelEl = document.getElementById('modal-model');
    const rawJsonEl = document.getElementById('modal-raw-json');

    if (seedEl) seedEl.innerText = '-';
    if (stepsEl) stepsEl.innerText = '-';
    if (cfgEl) cfgEl.innerText = '-';
    if (samplerEl) samplerEl.innerText = '-';
    if (modelEl) modelEl.innerText = '-';
    if (rawJsonEl) rawJsonEl.innerText = '{}';

    const fullUrl = `/api/images/${encodeURIComponent(relativePath)}`;
    if (modalImg) modalImg.src = fullUrl;
    if (modalDownload) {
        modalDownload.href = fullUrl;
        modalDownload.download = cleanFilename;
    }

    try {
        const res = await fetch(`/api/images/${encodeURIComponent(relativePath)}/metadata`);
        if (!res.ok) throw new Error('Metadata not found');
        const meta = await res.json();
        meta.relative_path = cleanRelPath(meta.relative_path);
        state.currentModalImage = meta;

        if (modalFilename) modalFilename.innerText = meta.filename || cleanFilename;
        if (modalDate) modalDate.innerText = `${meta.modified_at ? new Date(meta.modified_at).toLocaleString() : ''} · ${meta.file_size_human || ''}`;
        if (modalDim) modalDim.innerText = meta.dimensions || '';

        const p = meta.parsed || {};
        if (posPromptEl) posPromptEl.innerText = p.positive_prompt || '(None found)';
        if (negPromptEl) negPromptEl.innerText = p.negative_prompt || '(None)';
        if (seedEl) seedEl.innerText = p.seed !== undefined && p.seed !== null ? p.seed : '-';
        if (stepsEl) stepsEl.innerText = p.steps || '-';
        if (cfgEl) cfgEl.innerText = p.cfg || '-';
        if (samplerEl) samplerEl.innerText = p.sampler_name ? `${p.sampler_name} / ${p.scheduler || 'normal'}` : '-';
        if (modelEl) modelEl.innerText = p.model || '(Standard)';

        if (rawJsonEl) rawJsonEl.innerText = JSON.stringify(meta.raw || {}, null, 2);

        // Render clickable tag pills
        renderModalPromptTags(p.positive_prompt);

    } catch (e) {
        console.warn('Metadata fetch error:', e);
        if (modalDate) modalDate.innerText = 'No metadata embedded';
        if (posPromptEl) posPromptEl.innerText = '(No metadata chunk found)';
    }
}

function renderModalPromptTags(promptText) {
    const container = document.getElementById('modal-pos-tags');
    if (!container) return;
    container.innerHTML = '';
    if (!promptText) {
        container.innerHTML = '<span class="text-[11px] text-zinc-500 italic">No prompt tags found</span>';
        return;
    }

    const tags = promptText.split(',').map(s => s.trim()).filter(Boolean);
    tags.slice(0, 150).forEach(t => {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'tag-pill-btn bg-pink-500/15 hover:bg-pink-500/35 text-pink-200 border border-pink-500/35 text-[10.5px] px-2.5 py-1 rounded-full font-medium transition-all hover:scale-105 active:scale-95';
        pill.innerText = t;
        pill.title = `Click to copy "${t}"`;
        pill.onclick = () => {
            navigator.clipboard.writeText(t);
            showToast(`(づ｡◕‿‿◕｡)づ Copied tag: ${t}`, 'info');
        };
        container.appendChild(pill);
    });
}

function downloadModalImage() {
    const item = state.currentModalImage || (state.galleryImages && state.galleryImages[state.currentModalIndex]);
    if (!item) return;
    const path = cleanRelPath(item.relative_path || item.filename);
    const link = document.createElement('a');
    link.href = `/api/images/${encodeURIComponent(path)}`;
    link.download = item.filename || path.split('/').pop() || 'comfy_image.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function copyModalField(field) {
    if (!state.currentModalImage) return;
    const p = state.currentModalImage.parsed || {};
    const text = field === 'positive_prompt' ? p.positive_prompt : p.negative_prompt;
    if (text) {
        navigator.clipboard.writeText(text);
        showToast('Copied prompt to clipboard! 📋✨', 'info');
    }
}

function loadModalPromptToGenerator() {
    loadModalPromptIntoGenerator();
}

function navigateModalImage(direction) {
    if (state.galleryImages.length === 0) return;
    const nextIndex = state.currentModalIndex + direction;
    openImageModalByIndex(nextIndex);
}

function closeImageModal() {
    document.getElementById('image-modal').classList.add('hidden');
    state.currentModalImage = null;
}

function toggleModalRawJson() {
    const container = document.getElementById('modal-raw-container');
    const arrow = document.getElementById('raw-toggle-arrow');
    if (!container) return;
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        if (arrow) arrow.innerText = '▲';
    } else {
        container.classList.add('hidden');
        if (arrow) arrow.innerText = '▼';
    }
}

function copyModalPositive() {
    copyModalField('positive_prompt');
}

function copyModalNegative() {
    copyModalField('negative_prompt');
}

function copyModalRawJson() {
    const el = document.getElementById('modal-raw-json');
    if (el && el.innerText) {
        navigator.clipboard.writeText(el.innerText);
        showToast('Raw JSON copied to clipboard', 'info');
    }
}

function loadModalPromptIntoGenerator() {
    if (!state.currentModalImage) return;
    const p = state.currentModalImage.parsed || {};

    if (p.positive_prompt) document.getElementById('positive-prompt').value = p.positive_prompt;
    if (p.negative_prompt) document.getElementById('negative-prompt').value = p.negative_prompt;
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
    if (p.sampler_name) document.getElementById('sampler-select').value = p.sampler_name;
    if (p.scheduler) document.getElementById('scheduler-select').value = p.scheduler;

    closeImageModal();
    switchTab('generator');
    showToast('(ﾉ◕ヮ◕)ﾉ*:･ﾟ Loaded into Generator! ✨', 'success');
}


// ==========================================
// REEL MODE FULLSCREEN RANDOM VIEWER & SWIPING
// ==========================================
function openTikTokMode() {
    if (state.galleryImages.length === 0) {
        showToast('No images available in gallery', 'error');
        return;
    }

    // Shuffle queue randomly
    state.tiktokQueue = [...state.galleryImages].sort(() => Math.random() - 0.5);
    state.tiktokIndex = 0;

    const modal = document.getElementById('tiktok-modal');
    if (modal) modal.classList.remove('hidden');

    displayTikTokItem(0, 'none');
}
const openReelMode = openTikTokMode;

async function displayTikTokItem(index, direction = 'up') {
    if (state.tiktokQueue.length === 0) return;
    if (index < 0) index = state.tiktokQueue.length - 1;
    if (index >= state.tiktokQueue.length) index = 0;
    state.tiktokIndex = index;

    const item = state.tiktokQueue[index];
    const clean = cleanRelPath(item.relative_path);
    state.currentTikTokImage = item;

    const img = document.getElementById('tiktok-img');
    if (img) {
        img.classList.remove('reel-slide-up', 'reel-slide-down', 'tiktok-slide-up');
        void img.offsetWidth; // Trigger reflow for animation
        img.src = `/api/images/${encodeURIComponent(clean)}`;
        if (direction === 'down') {
            img.classList.add('reel-slide-down');
        } else if (direction === 'up') {
            img.classList.add('reel-slide-up');
        }
    }

    const fn = document.getElementById('tiktok-filename');
    if (fn) fn.innerText = item.filename;
    const cnt = document.getElementById('tiktok-index-counter');
    if (cnt) cnt.innerText = `${index + 1} / ${state.tiktokQueue.length}`;
    const badge = document.getElementById('tiktok-meta-badge');
    if (badge) badge.innerText = item.file_size_human;

    // Fetch embedded prompt info
    try {
        const res = await fetch(`/api/images/${encodeURIComponent(clean)}/metadata`);
        if (res.ok) {
            const meta = await res.json();
            state.currentTikTokImage = meta;
            const p = meta.parsed || {};
            const prText = document.getElementById('tiktok-prompt-text');
            if (prText) prText.innerText = p.positive_prompt || '(No prompt embedded)';
            if (badge) badge.innerText = `${p.seed ? 'Seed: ' + p.seed + ' · ' : ''}${meta.file_size_human}`;
            
            // Render interactive tags for quick copying
            const tagContainer = document.getElementById('tiktok-prompt-tags');
            if (tagContainer) {
                tagContainer.innerHTML = '';
                if (p.positive_prompt) {
                    const tags = p.positive_prompt.split(',').map(s => s.trim()).filter(Boolean);
                    tags.slice(0, 15).forEach(t => {
                        const pill = document.createElement('span');
                        pill.className = 'tag-pill-btn bg-pink-500/20 text-pink-200 border border-pink-500/30 text-[10px] cursor-pointer hover:bg-pink-500/40';
                        pill.innerText = t;
                        pill.onclick = () => {
                            navigator.clipboard.writeText(t);
                            showToast(`(づ｡◕‿‿◕｡)づ Copied tag: ${t}`, 'info');
                        };
                        tagContainer.appendChild(pill);
                    });
                }
            }
        }
    } catch (e) {
        const prText = document.getElementById('tiktok-prompt-text');
        if (prText) prText.innerText = '';
    }
}

function nextTikTokImage(direction = 'up') {
    if (state.tiktokQueue.length === 0) return;
    displayTikTokItem(state.tiktokIndex + 1, direction);
}
const nextReelImage = nextTikTokImage;

function prevTikTokImage(direction = 'down') {
    if (state.tiktokQueue.length === 0) return;
    displayTikTokItem(state.tiktokIndex - 1, direction);
}
const prevReelImage = prevTikTokImage;

function handleTikTokWheel(e) {
    if (Math.abs(e.deltaY) > 30) {
        if (e.deltaY > 0) nextTikTokImage('up');
        else prevTikTokImage('down');
    }
}

// Swiping support for Reel Mode (Mouse drag & Touch swipe)
let reelTouchStartY = 0;
let reelIsMouseDown = false;
let reelMouseStartY = 0;

function initReelSwipe() {
    const reelArea = document.getElementById('reel-swipe-container') || document.getElementById('tiktok-modal');
    if (!reelArea) return;

    // Mobile/tablet touch swipe
    reelArea.addEventListener('touchstart', (e) => {
        if (e.touches && e.touches.length === 1) {
            reelTouchStartY = e.touches[0].clientY;
        }
    }, { passive: true });

    reelArea.addEventListener('touchend', (e) => {
        if (e.changedTouches && e.changedTouches.length === 1) {
            const deltaY = reelTouchStartY - e.changedTouches[0].clientY;
            if (deltaY > 35) {
                nextTikTokImage('up');
            } else if (deltaY < -35) {
                prevTikTokImage('down');
            }
        }
    }, { passive: true });

    // Desktop mouse drag swipe
    reelArea.addEventListener('mousedown', (e) => {
        if (e.button === 0) {
            reelIsMouseDown = true;
            reelMouseStartY = e.clientY;
        }
    });

    window.addEventListener('mouseup', (e) => {
        if (reelIsMouseDown) {
            reelIsMouseDown = false;
            const deltaY = reelMouseStartY - e.clientY;
            if (deltaY > 35) {
                nextTikTokImage('up');
            } else if (deltaY < -35) {
                prevTikTokImage('down');
            }
        }
    });
}

function toggleTikTokAutoplay() {
    const btn = document.getElementById('btn-tiktok-autoplay');
    const icon = document.getElementById('tiktok-autoplay-icon');
    const text = document.getElementById('tiktok-autoplay-text');

    if (state.tiktokAutoplayActive) {
        clearInterval(state.tiktokAutoplayInterval);
        state.tiktokAutoplayActive = false;
        if (icon) icon.innerText = '▶';
        if (text) text.innerText = 'Auto-Play';
        if (btn) btn.classList.remove('bg-pink-600', 'text-white');
    } else {
        state.tiktokAutoplayActive = true;
        if (icon) icon.innerText = '⏸';
        if (text) text.innerText = 'Playing (5s)';
        if (btn) btn.classList.add('bg-pink-600', 'text-white');
        state.tiktokAutoplayInterval = setInterval(() => nextTikTokImage('up'), 5000);
    }
}
const toggleReelAutoplay = toggleTikTokAutoplay;

function closeTikTokMode() {
    if (state.tiktokAutoplayActive) toggleTikTokAutoplay();
    const modal = document.getElementById('tiktok-modal');
    if (modal) modal.classList.add('hidden');
    state.currentTikTokImage = null;
}
const closeReelMode = closeTikTokMode;

function copyTikTokPrompt() {
    const text = document.getElementById('tiktok-prompt-text').innerText;
    if (text) {
        navigator.clipboard.writeText(text);
        showToast('(づ｡◕‿‿◕｡)づ Prompt copied to clipboard! ✨', 'info');
    }
}
const copyReelPrompt = copyTikTokPrompt;

function loadTikTokPromptIntoGenerator() {
    if (!state.currentTikTokImage) return;
    const p = state.currentTikTokImage.parsed || {};
    if (p.positive_prompt) document.getElementById('positive-prompt').value = p.positive_prompt;
    if (p.negative_prompt) document.getElementById('negative-prompt').value = p.negative_prompt;
    if (p.seed) {
        document.getElementById('seed-input').value = p.seed;
        document.getElementById('seed-randomize').checked = false;
        toggleSeedInput();
    }
    closeTikTokMode();
    switchTab('generator');
    showToast('(ﾉ◕ヮ◕)ﾉ*:･ﾟ Loaded into Generator! ✨', 'success');
}
const loadReelPromptIntoGenerator = loadTikTokPromptIntoGenerator;

// ==========================================
// INTERNAL DELETE CONFIRMATION MODAL
// ==========================================
function openDeleteConfirmModal(imageObj) {
    if (!imageObj) {
        const imgModal = document.getElementById('image-modal');
        const tiktokModal = document.getElementById('tiktok-modal');
        if (imgModal && !imgModal.classList.contains('hidden')) {
            imageObj = state.currentModalImage || (state.galleryImages && state.galleryImages[state.currentModalIndex]);
        } else if (tiktokModal && !tiktokModal.classList.contains('hidden')) {
            imageObj = state.currentTikTokImage || (state.tiktokQueue && state.tiktokQueue[state.tiktokIndex]);
        }
    }
    if (!imageObj) return;
    state.imagePendingDelete = imageObj;

    const modal = document.getElementById('delete-confirm-modal');
    const clean = cleanRelPath(imageObj.relative_path || imageObj.filename);
    
    document.getElementById('delete-modal-filename').innerText = imageObj.filename || clean.split('/').pop();
    document.getElementById('delete-modal-size').innerText = `Size: ${imageObj.file_size_human || 'Unknown'}`;
    document.getElementById('delete-modal-thumb').src = `/api/thumbnails/${encodeURIComponent(clean)}`;

    modal.classList.remove('hidden');
}

function closeDeleteConfirmModal() {
    document.getElementById('delete-confirm-modal').classList.add('hidden');
    state.imagePendingDelete = null;
}

async function executePermanentDelete() {
    if (!state.imagePendingDelete) return;

    const imageObj = state.imagePendingDelete;
    const clean = cleanRelPath(imageObj.relative_path || imageObj.filename);
    const filename = imageObj.filename || clean.split('/').pop();

    const btn = document.getElementById('btn-execute-delete');
    btn.disabled = true;
    btn.innerText = 'Deleting...';

    try {
        const res = await fetch(`/api/images/${encodeURIComponent(clean)}`, {
            method: 'DELETE'
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Server error' }));
            throw new Error(err.detail || 'Delete failed');
        }

        showToast(`Permanently deleted: ${filename}`, 'success');
        closeDeleteConfirmModal();

        // If inside regular detail modal, advance to next image or close
        if (!document.getElementById('image-modal').classList.contains('hidden')) {
            if (state.galleryImages.length > 1) {
                navigateModalImage(1);
            } else {
                closeImageModal();
            }
        }

        // If inside TikTok modal, advance
        if (!document.getElementById('tiktok-modal').classList.contains('hidden')) {
            if (state.tiktokQueue.length > 1) {
                state.tiktokQueue = state.tiktokQueue.filter(i => cleanRelPath(i.relative_path) !== clean);
                nextTikTokImage();
            } else {
                closeTikTokMode();
            }
        }

        // If preview was showing this image, reset
        if (state.currentPreviewData && cleanRelPath(state.currentPreviewData.relative_path) === clean) {
            document.getElementById('preview-image-wrapper').classList.add('hidden');
            document.getElementById('preview-meta-footer').classList.add('hidden');
            document.getElementById('preview-placeholder').classList.remove('hidden');
            state.currentPreviewData = null;
        }

    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = 'Delete Permanently';
    }
}

// ==========================================
// WORKFLOW EDITOR VIEW LOGIC
// ==========================================
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
            const data = await res.json();
            if (data.parsed_params) {
                applyWorkflowParamsToForm(data.parsed_params, 'Editor Workflow');
            }
            showToast('Applied workflow template to Generator ✨', 'success');
        } else {
            throw new Error('Failed to save');
        }
    } catch (e) {
        showToast(`Invalid JSON: ${e.message}`, 'error');
    }
}

async function resetWorkflowTemplate() {
    try {
        const res = await fetch('/api/workflow/reset', { method: 'POST' });
        const data = await res.json();
        if (data.parsed_params) {
            applyWorkflowParamsToForm(data.parsed_params, 'Default Workflow');
        }
        document.getElementById('workflow-editor').value = JSON.stringify(data.workflow, null, 2);
        showToast('Reset to default workflow ↺', 'info');
        await loadAvailableWorkflows();
    } catch (e) {
        showToast('Failed to reset workflow', 'error');
    }
}

// ==========================================
// GLOBAL KEYBOARD SHORTCUTS
// ==========================================
document.addEventListener('keydown', (e) => {
    // Escape closes modals
    if (e.key === 'Escape') {
        const deleteModal = document.getElementById('delete-confirm-modal');
        if (!deleteModal.classList.contains('hidden')) {
            closeDeleteConfirmModal();
            return;
        }
        const tiktokModal = document.getElementById('tiktok-modal');
        if (!tiktokModal.classList.contains('hidden')) {
            closeTikTokMode();
            return;
        }
        const imgModal = document.getElementById('image-modal');
        if (!imgModal.classList.contains('hidden')) {
            closeImageModal();
            return;
        }
    }

    // Modal navigation with Left / Right arrows
    const imgModal = document.getElementById('image-modal');
    if (!imgModal.classList.contains('hidden')) {
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            navigateModalImage(-1);
            return;
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            navigateModalImage(1);
            return;
        }
    }

    // TikTok mode keyboard navigation
    const tiktokModal = document.getElementById('tiktok-modal');
    if (!tiktokModal.classList.contains('hidden')) {
        if (e.key === 'ArrowDown' || e.key === ' ') {
            e.preventDefault();
            nextTikTokImage();
            return;
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            prevTikTokImage();
            return;
        }
    }

    // Ctrl+Enter or Cmd+Enter to generate
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (state.activeTab === 'generator' && !state.isGenerating) {
            handleGenerate();
        }
    }
});

// Global drag & drop support for workflow files (.json / .png)
window.addEventListener('dragover', (e) => {
    e.preventDefault();
});
window.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        const file = e.dataTransfer.files[0];
        const lower = file.name.toLowerCase();
        if (lower.endsWith('.json') || lower.endsWith('.png') || lower.endsWith('.webp')) {
            e.preventDefault();
            handleWorkflowFileInput(e.dataTransfer.files);
        }
    }
});

// Initialization
window.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    checkComfyStatus();
    loadModels();
    loadGallery(true);
    loadAvailableWorkflows();
    preloadTopTags();
    initTagAutocomplete();
    initReelSwipe();

    setInterval(checkComfyStatus, 10000);
});

