/* Fetch effective addon policy without blocking or reflowing the server page. */
(() => {
    const section = document.querySelector('[data-bandwidth-url]');
    if (!section) return;
    const value = (name, text) => {
        const field = section.querySelector(`[data-bandwidth-value="${name}"]`);
        if (field) field.textContent = text;
    };
    const note = section.querySelector('[data-bandwidth-note]');
    const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
    const load = async () => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(section.dataset.bandwidthUrl, {
                credentials: 'same-origin', headers: { Accept: 'application/json' },
                cache: 'no-store', signal: controller.signal,
            });
            if (!response.ok) throw new Error('Unavailable');
            const policy = await response.json();
            if (!policy.available) {
                note.textContent = 'Bandwidth reporting is not available for this server.';
                return;
            }
            value('allowance', policy.limit_gb ? `${number.format(policy.limit_gb)} GB` : 'Unlimited');
            value('usage', `${number.format(policy.used_gb)} GB`);
            value('speed', policy.speed_mbps ? `${number.format(policy.speed_mbps)} Mbps` : 'Unlimited');
            value('overage', !policy.limit_gb ? 'No overage limit' :
                policy.overage_action === 'throttle' ? `Throttled to ${number.format(policy.overage_mbps)} Mbps` : 'Outbound traffic blocked');
            note.textContent = policy.exhausted ?
                'Monthly allowance reached. The overage policy applies until this server’s monthly cycle resets.' :
                'Monthly outbound usage uses decimal GB and resets on this server’s monthly creation anniversary.';
        } catch (_) {
            note.textContent = 'Bandwidth information is temporarily unavailable. Refresh the page to try again.';
        } finally {
            clearTimeout(timer);
        }
    };
    if (document.readyState === 'complete') load();
    else window.addEventListener('load', load, { once: true });
})();
