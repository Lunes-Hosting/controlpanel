const storeDataEl = document.getElementById('store-data');
                const parsedProducts = storeDataEl?.dataset.products ? JSON.parse(storeDataEl.dataset.products) : [];
                const checkoutTemplate = storeDataEl?.dataset.checkoutTemplate || '';
                const creditFormatter = new Intl.NumberFormat('en-US');
                const priceLinkDict = parsedProducts.map((product) => {
                    const creditValue = Number(product.price || 0);
                    return {
                        link: product.price_link ? checkoutTemplate.replace('__PRICE_LINK__', encodeURIComponent(product.price_link)) : '#',
                        credits: creditValue,
                        creditsLabel: creditFormatter.format(Math.round(creditValue)),
                        disabled: !product.price_link
                    };
                });

                const rangeInput = document.getElementById('labels-range-input');
                const sliderValue = document.getElementById('slider-value');
                const costValue = document.getElementById('costValue');
                const purchaseBtn = document.getElementById('purchaseLink');
                const purchaseText = purchaseBtn ? purchaseBtn.querySelector('span') : null;
                const selectedCredits = document.getElementById('selectedCredits');

                const calculateCost = (credits) => {
                    const cost = parseFloat(credits || 0) / 100;
                    return cost.toFixed(2);
                };

                const updatePackageDisplay = (index) => {
                    if (!priceLinkDict.length) return;
                    const pkg = priceLinkDict[index];
                    const creditLabel = `${pkg.creditsLabel} Lunes Credits`;
                    if (sliderValue) sliderValue.textContent = creditLabel;
                    if (selectedCredits) selectedCredits.textContent = creditLabel;
                    if (costValue) costValue.textContent = `$${calculateCost(pkg.credits)}`;
                    if (purchaseBtn) {
                        if (pkg.disabled || pkg.link === '#') {
                            purchaseBtn.classList.add('pointer-events-none', 'opacity-60');
                            purchaseBtn.setAttribute('href', '#');
                            if (purchaseText) purchaseText.textContent = 'Unavailable';
                        } else {
                            purchaseBtn.classList.remove('pointer-events-none', 'opacity-60');
                            purchaseBtn.setAttribute('href', pkg.link);
                            if (purchaseText) purchaseText.textContent = 'Purchase Credits';
                        }
                    }
                };

                const initializeStore = () => {
                    if (!rangeInput || !priceLinkDict.length) {
                        if (sliderValue) sliderValue.textContent = 'No packages available';
                        if (selectedCredits) selectedCredits.textContent = 'No packages available';
                        if (costValue) costValue.textContent = '$0.00';
                        if (purchaseBtn) {
                            purchaseBtn.classList.add('pointer-events-none', 'opacity-60');
                            purchaseBtn.setAttribute('href', '#');
                        }
                        return;
                    }
                    rangeInput.max = Math.max(priceLinkDict.length - 1, 0);
                    rangeInput.value = 0;
                    updatePackageDisplay(0);
                    rangeInput.addEventListener('input', () => updatePackageDisplay(rangeInput.value));
                };

                initializeStore();

function openDeleteModal() {
            document.getElementById('deleteModal').style.display = 'block';
        }

        function closeDeleteModal() {
            document.getElementById('deleteModal').style.display = 'none';
        }

// Server display data can refresh independently of the live account/balance.
(() => {
    const body = document.body;
    const container = document.getElementById('dashboard-servers');
    const notice = document.getElementById('dashboard-inventory-status');
    let ready = body.dataset.inventoryReady === 'true';
    let stopped = false;
    let running = false;
    let timer = null;
    let lastHTML = container?.innerHTML;

    async function refresh() {
        if (stopped || running) return;
        if (document.hidden) { timer = setTimeout(refresh, 15000); return; }
        running = true;
        let delay = 15000;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8000);
        try {
            const response = await fetch(body.dataset.dashboardInventoryUrl, {
                credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                headers: {'Accept': 'application/json', 'X-Dashboard-Account': body.dataset.dashboardAccount}
            });
            if (response.redirected || [401, 403, 409].includes(response.status) ||
                (response.status === 200 && !response.headers.get('Content-Type')?.includes('application/json'))) {
                if (notice) { notice.hidden = false; notice.textContent = 'Your account or session changed. Reload to continue.'; }
                stopped = true;
                return;
            }
            if (!response.ok && response.status !== 503) throw new Error('Inventory unavailable');
            const state = await response.json();
            ready = state.ready;
            if (container) {
                if (lastHTML !== state.html && !container.contains(document.activeElement)) {
                    container.innerHTML = state.html;
                    lastHTML = state.html;
                }
                container.setAttribute('aria-busy', ready ? 'false' : 'true');
            }
            document.querySelectorAll('[data-dashboard-stat]').forEach(element => {
                const value = state[element.dataset.dashboardStat];
                if (value !== null && value !== undefined) element.textContent = value;
            });
            const meter = document.querySelector('.credit-meter');
            if (meter && ready) {
                meter.setAttribute('aria-valuenow', state.credit_width);
                document.querySelector('[data-dashboard-meter-fill]').style.setProperty('--progress-width', state.credit_width);
                document.querySelector('[data-dashboard-meter-label]').textContent = `${state.credit_width}% / 100%`;
            }
            if (notice) {
                notice.hidden = !ready || (!state.error && !state.refreshing);
                notice.textContent = state.error ? 'Showing last known server inventory. Retrying…' : 'Updating server inventory…';
            }
            delay = state.error ? 15000 : state.refreshing ? 2000 : 15000;
        } catch (error) {
            if (notice) { notice.hidden = false; notice.textContent = 'Server inventory temporarily unavailable. Retrying…'; }
        } finally {
            clearTimeout(timeout);
            running = false;
            if (!stopped) timer = setTimeout(refresh, delay);
        }
    }
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) { clearTimeout(timer); refresh(); }
    });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
    window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; refresh(); } });
    if (ready && body.dataset.inventoryRefreshing !== 'true') timer = setTimeout(refresh, 15000);
    else refresh();
})();
