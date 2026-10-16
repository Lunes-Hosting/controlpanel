(() => {
    const container = document.querySelector('[data-stats-url]');
    const status = document.getElementById('stats-status');
    let data = JSON.parse(document.getElementById('stats-initial-data').textContent);
    let chart = null;
    let timer = null;
    let running = false;
    let stopped = false;

    function showStatus(message) {
        if (status.textContent !== message) status.textContent = message;
    }

    function renderChart() {
        if (!window.Chart || !data.chart_labels) return;
        if (chart) {
            chart.data.labels = data.chart_labels;
            chart.data.datasets[0].data = data.chart_values;
            chart.update('none');
            return;
        }
        chart = new Chart(document.getElementById('plansChart'), {
            type: 'bar',
            data: {labels: data.chart_labels, datasets: [{
                label: 'Servers by Plan', data: data.chart_values,
                backgroundColor: 'rgba(59, 130, 246, 0.6)', borderColor: 'rgba(59, 130, 246, 1)',
                borderWidth: 1, borderRadius: 4, maxBarThickness: 40
            }]},
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {legend: {display: false}, tooltip: {
                    backgroundColor: '#1e293b', titleColor: '#f1f5f9', bodyColor: '#cbd5e1',
                    borderColor: '#334155', borderWidth: 1, padding: 10, displayColors: false
                }},
                scales: {
                    x: {grid: {display: false, color: '#334155'}, ticks: {color: '#94a3b8'}},
                    y: {beginAtZero: true, grid: {color: '#334155'}, ticks: {color: '#94a3b8'}}
                }
            }
        });
    }

    function renderMetrics() {
        container.querySelectorAll('[data-stat]').forEach(element => {
            const value = data[element.dataset.stat];
            if (value !== undefined && value !== null) element.textContent = value;
        });
        renderChart();
    }

    async function refresh() {
        if (running || stopped) return;
        if (document.hidden) {
            timer = setTimeout(refresh, 30000);
            return;
        }
        running = true;
        let delay = 60000;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(container.dataset.statsUrl, {
                credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                headers: {'Accept': 'application/json'}
            });
            if (response.redirected || response.status === 401 || response.status === 403 ||
                (response.status === 200 && !response.headers.get('Content-Type')?.includes('application/json'))) {
                showStatus('Your session has expired or access changed. Reload to sign in.');
                stopped = true;
                return;
            }
            if (!response.ok && response.status !== 503) throw new Error('Refresh unavailable');
            const state = await response.json();
            if (state.data) {
                data = state.data;
                renderMetrics();
            }
            showStatus(state.error ? (state.data ? 'Showing the last available statistics. Retrying…' :
                'Statistics temporarily unavailable. Retrying…') :
                state.refreshing ? 'Updating statistics…' : 'Statistics updated. Refreshes every minute.');
            delay = state.error ? 15000 : state.refreshing ? 2000 : 60000;
        } catch (error) {
            showStatus('Statistics refresh unavailable. Retrying shortly…');
            delay = 15000;
        } finally {
            clearTimeout(timeout);
            running = false;
            if (!stopped) timer = setTimeout(refresh, delay);
        }
    }

    document.getElementById('stats-chart-library').addEventListener('load', renderChart);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            clearTimeout(timer);
            refresh();
        }
    });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
    window.addEventListener('pageshow', event => {
        if (event.persisted) { stopped = false; refresh(); }
    });
    renderMetrics();
    refresh();
})();
