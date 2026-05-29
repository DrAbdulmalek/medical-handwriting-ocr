// Dictionary Management JavaScript

const API_BASE = 'http://localhost:8000/api';

class DictionaryManager {
    constructor() {
        this.token = localStorage.getItem('dictionary_token');
        this.dictionaries = [];
        this.init();
    }

    init() {
        this.checkTokenStatus();
        this.loadDictionaries();
    }

    async checkTokenStatus() {
        const statusEl = document.getElementById('token-status');

        if (!this.token) {
            statusEl.innerHTML = `
                <div class="status-indicator invalid">
                    <span class="dot"></span>
                    <span>لم يتم إعداد Token</span>
                </div>`;
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/dictionaries/status`, {
                headers: { 'X-Dictionary-Token': this.token }
            });
            const data = await response.json();

            if (data.enabled) {
                statusEl.innerHTML = `
                    <div class="status-indicator valid">
                        <span class="dot"></span>
                        <span>متصل (${data.dictionaries_available} قاموس)</span>
                    </div>`;
                document.getElementById('token-section').style.display = 'none';
            } else {
                statusEl.innerHTML = `
                    <div class="status-indicator invalid">
                        <span class="dot"></span>
                        <span>Token غير صالح</span>
                    </div>`;
            }
        } catch (error) {
            statusEl.innerHTML = `
                <div class="status-indicator invalid">
                    <span class="dot"></span>
                    <span>خطأ في الاتصال</span>
                </div>`;
        }
    }

    async validateAndSaveToken() {
        const input = document.getElementById('github-token');
        const token = input.value.trim();
        const resultEl = document.getElementById('token-validation-result');

        if (!token.startsWith('ghp_')) {
            resultEl.innerHTML = '<p class="error">❌ يجب أن يبدأ Token بـ ghp_</p>';
            return;
        }

        resultEl.innerHTML = '<p>⏳ جاري التحقق...</p>';

        try {
            const response = await fetch(`${API_BASE}/dictionaries/status`, {
                headers: { 'X-Dictionary-Token': token }
            });
            const data = await response.json();

            if (data.enabled) {
                this.token = token;
                localStorage.setItem('dictionary_token', token);
                resultEl.innerHTML = `<p class="success">✅ تم الاتصال! ${data.dictionaries_available} قاموس متاح</p>`;
                setTimeout(() => location.reload(), 1500);
            } else {
                resultEl.innerHTML = `<p class="error">❌ ${data.reason || 'Token غير صالح'}</p>`;
            }
        } catch (error) {
            resultEl.innerHTML = `<p class="error">❌ خطأ: ${error.message}</p>`;
        }
    }

    useDemoMode() {
        localStorage.removeItem('dictionary_token');
        alert('الوضع التجريبي محدود - بعض القواميس لن تكون متاحة');
        location.reload();
    }

    async loadDictionaries() {
        const grid = document.getElementById('dictionaries-grid');

        try {
            const headers = {};
            if (this.token) headers['X-Dictionary-Token'] = this.token;

            const response = await fetch(`${API_BASE}/dictionaries/list`, { headers });
            const data = await response.json();

            this.dictionaries = data.dictionaries || [];
            this.renderDictionaries();
            this.updateStats(data);
        } catch (error) {
            grid.innerHTML = `
                <div class="error-state">
                    <p>❌ فشل تحميل القواميس</p>
                    <button onclick="location.reload()">إعادة المحاولة</button>
                </div>`;
        }
    }

    renderDictionaries() {
        const grid = document.getElementById('dictionaries-grid');

        if (this.dictionaries.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <p>لا توجد قواميس متاحة</p>
                    <p>أضف Token للوصول إلى القواميس</p>
                </div>`;
            return;
        }

        grid.innerHTML = this.dictionaries.map(dict => `
            <div class="dict-card">
                <div class="dict-header">
                    <span class="dict-icon">📖</span>
                    <span class="dict-status active">نشط</span>
                </div>
                <h4>${dict.name}</h4>
                <div class="dict-meta">
                    <span>📁 ${dict.path}</span>
                </div>
            </div>
        `).join('');

        const select = document.getElementById('dict-select');
        select.innerHTML = '<option value="">جميع القواميس</option>' +
            this.dictionaries.map(d => `<option value="${d.name}">${d.name}</option>`).join('');
    }

    updateStats(data) {
        document.getElementById('stat-total-dicts').textContent = data.dictionaries?.length || 0;
        document.getElementById('stat-total-entries').textContent = data.total_entries || '-';
        document.getElementById('stat-last-sync').textContent = data.last_sync || 'لم تتم';
    }

    async searchDictionaries() {
        const term = document.getElementById('dict-search-input').value.trim();
        const dictName = document.getElementById('dict-select').value;
        const resultsEl = document.getElementById('search-results');

        if (!term) return;

        resultsEl.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

        try {
            const headers = {};
            if (this.token) headers['X-Dictionary-Token'] = this.token;

            const url = new URL(`${API_BASE}/dictionaries/search`);
            url.searchParams.append('term', term);
            if (dictName) url.searchParams.append('dictionaries', dictName);

            const response = await fetch(url, { headers });
            const data = await response.json();

            this.renderSearchResults(data);
        } catch (error) {
            resultsEl.innerHTML = `<p class="error">❌ ${error.message}</p>`;
        }
    }

    renderSearchResults(data) {
        const resultsEl = document.getElementById('search-results');

        if (data.results_found === 0) {
            resultsEl.innerHTML = '<p>لا توجد نتائج</p>';
            return;
        }

        resultsEl.innerHTML = `
            <p>تم العثور على ${data.results_found} نتيجة</p>
            <div class="results-list">
                ${data.results.map(r => `
                    <div class="result-item">
                        <strong>${r.term}</strong>
                        <span class="dict-tag">${r.dictionary}</span>
                        <p>${r.definition || 'لا يوجد تعريف'}</p>
                    </div>
                `).join('')}
            </div>`;
    }

    async syncDictionaries() {
        if (!this.token) {
            alert('يجب إعداد Token أولاً');
            return;
        }

        const btn = event.target;
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> جاري المزامنة...';

        try {
            const response = await fetch(`${API_BASE}/dictionaries/sync?force=true`, {
                method: 'POST',
                headers: { 'X-Dictionary-Token': this.token }
            });

            const data = await response.json();
            if (data.success) {
                alert(`✅ تمت المزامنة!`);
                this.loadDictionaries();
            } else {
                alert(`❌ فشل: ${data.error}`);
            }
        } catch (error) {
            alert(`❌ خطأ: ${error.message}`);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<span>🔄</span> مزامنة';
        }
    }
}

const dictManager = new DictionaryManager();
