// Medical OCR Frontend Application
const API_BASE = 'http://localhost:8000/api';

class MedicalOCRApp {
    constructor() {
        this.currentDocument = null;
        this.regions = [];
        this.currentIndex = 0;
        this.corrections = new Map();

        this.init();
    }

    init() {
        // Upload handling
        const dropZone = document.getElementById('drop-zone');
        const fileInput = document.getElementById('file-input');

        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length) this.handleUpload(files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) this.handleUpload(e.target.files[0]);
        });

        // Navigation
        document.getElementById('prev-word').addEventListener('click', () => this.prevWord());
        document.getElementById('next-word').addEventListener('click', () => this.nextWord());
        document.getElementById('save-all').addEventListener('click', () => this.saveAll());

        // Correction
        document.getElementById('confirm-correct').addEventListener('click', () => this.confirmCorrection());
        document.getElementById('skip-word').addEventListener('click', () => this.skipWord());
        document.getElementById('flag-medical').addEventListener('click', () => this.flagMedical());

        // Modal
        document.querySelector('.close').addEventListener('click', () => this.closeModal());
        document.getElementById('modal-save').addEventListener('click', () => this.saveModalCorrection());
        document.getElementById('modal-cancel').addEventListener('click', () => this.closeModal());
    }

    async handleUpload(file) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            // Show loading
            document.querySelector('.upload-content').innerHTML = `
                <span class="icon">⏳</span>
                <p>جاري معالجة الصورة...</p>
            `;

            const response = await fetch(`${API_BASE}/upload`, {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                this.loadDocument(data);
            } else {
                alert('Error: ' + data.detail);
            }
        } catch (error) {
            alert('Upload failed: ' + error.message);
        }
    }

    loadDocument(data) {
        this.currentDocument = data;
        this.regions = data.regions;
        this.currentIndex = 0;

        // Show editor
        document.getElementById('upload-section').classList.add('hidden');
        document.getElementById('editor-section').classList.remove('hidden');
        document.getElementById('stats-section').classList.remove('hidden');

        // Load image
        document.getElementById('document-image').src = URL.createObjectURL(
            document.getElementById('file-input').files[0]
        );

        this.updateStats();
        this.renderWordsList();
        this.selectWord(0);
    }

    selectWord(index) {
        if (index < 0 || index >= this.regions.length) return;

        this.currentIndex = index;
        const region = this.regions[index];

        // Update highlight box
        const img = document.getElementById('document-image');
        const box = document.getElementById('highlight-box');
        const bbox = region.bbox;

        // Calculate relative position
        const scaleX = img.clientWidth / img.naturalWidth;
        const scaleY = img.clientHeight / img.naturalHeight;

        box.style.left = (bbox.x1 * scaleX) + 'px';
        box.style.top = (bbox.y1 * scaleY) + 'px';
        box.style.width = ((bbox.x2 - bbox.x1) * scaleX) + 'px';
        box.style.height = ((bbox.y2 - bbox.y1) * scaleY) + 'px';

        // Update word card
        document.getElementById('predicted-text').textContent = region.predicted_text;
        document.getElementById('confidence-value').textContent = Math.round(region.confidence * 100) + '%';

        // Confidence color
        const badge = document.getElementById('confidence-badge');
        badge.className = 'confidence-badge';
        if (region.confidence > 0.9) badge.classList.add('confidence-high');
        else if (region.confidence > 0.7) badge.classList.add('confidence-medium');
        else badge.classList.add('confidence-low');

        // Crop preview
        if (region.crop_url) {
            document.getElementById('word-crop').src = region.crop_url;
        }

        // Correction input
        const input = document.getElementById('correction-input');
        input.value = this.corrections.get(region.id) || '';
        input.focus();

        // Update progress
        document.getElementById('progress').textContent =
            `كلمة ${index + 1} من ${this.regions.length}`;

        // Highlight in list
        document.querySelectorAll('.word-item').forEach((el, i) => {
            el.classList.toggle('active', i === index);
        });
    }

    prevWord() {
        if (this.currentIndex > 0) {
            this.selectWord(this.currentIndex - 1);
        }
    }

    nextWord() {
        if (this.currentIndex < this.regions.length - 1) {
            this.selectWord(this.currentIndex + 1);
        }
    }

    confirmCorrection() {
        const region = this.regions[this.currentIndex];
        const input = document.getElementById('correction-input');
        const corrected = input.value.trim();

        if (corrected && corrected !== region.predicted_text) {
            this.corrections.set(region.id, corrected);
            this.saveCorrection(region.id, corrected);
        }

        this.nextWord();
    }

    async saveCorrection(regionId, correctedText) {
        try {
            const response = await fetch(`${API_BASE}/correct`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    region_id: regionId,
                    corrected_text: correctedText,
                    user_id: 'user_' + Date.now()
                })
            });

            if (response.ok) {
                this.updateStats();
                this.renderWordsList();
            }
        } catch (error) {
            console.error('Save failed:', error);
        }
    }

    skipWord() {
        this.nextWord();
    }

    flagMedical() {
        // Flag for medical review
        alert('تم الإبلاغ عن مصطلح طبي للمراجعة');
    }

    async saveAll() {
        // Save all pending corrections
        const pending = Array.from(this.corrections.entries());

        if (pending.length === 0) {
            alert('لا يوجد تصحيحات لحفظها');
            return;
        }

        let saved = 0;
        for (const [regionId, text] of pending) {
            await this.saveCorrection(regionId, text);
            saved++;
        }

        alert(`تم حفظ ${saved} تصحيح بنجاح!`);
        this.corrections.clear();
    }

    renderWordsList() {
        const container = document.getElementById('words-container');
        container.innerHTML = '';

        this.regions.forEach((region, index) => {
            const div = document.createElement('div');
            div.className = 'word-item';
            div.innerHTML = `
                <span class="word-text">${region.predicted_text}</span>
                <span class="word-status status-${region.status}">${region.status}</span>
            `;
            div.addEventListener('click', () => this.selectWord(index));
            container.appendChild(div);
        });
    }

    updateStats() {
        document.getElementById('stat-total').textContent = this.regions.length;
        document.getElementById('stat-corrected').textContent =
            this.regions.filter(r => r.status === 'gold_standard').length;
        document.getElementById('stat-pending').textContent =
            this.regions.filter(r => r.status === 'pending').length;
    }

    // Modal functions
    openModal(region) {
        document.getElementById('modal-original').textContent = region.predicted_text;
        document.getElementById('modal-input').value = '';
        document.getElementById('modal-crop-img').src = region.crop_url || '';
        document.getElementById('correction-modal').classList.remove('hidden');
    }

    closeModal() {
        document.getElementById('correction-modal').classList.add('hidden');
    }

    saveModalCorrection() {
        const corrected = document.getElementById('modal-input').value.trim();
        if (corrected) {
            const region = this.regions[this.currentIndex];
            this.corrections.set(region.id, corrected);
            this.saveCorrection(region.id, corrected);
        }
        this.closeModal();
    }
}

// Initialize app
const app = new MedicalOCRApp();
